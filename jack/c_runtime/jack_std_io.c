#include "jack_std_io.h"

#include <errno.h>
#include <stdlib.h>
#include <sys/stat.h>

jack_str jack_string_view(const uint8_t *data, size_t length) {
    return (jack_str){(const char *)data, length};
}

jack_in_slice_u8 jack_bytes_view(const uint8_t *data, size_t length) {
    return (jack_in_slice_u8){data, (int32_t)length};
}

jack_slice_u8 jack_bytes_view_mut(uint8_t *data, size_t length) {
    return (jack_slice_u8){data, (int32_t)length};
}

uint8_t jack_str_byte(jack_str value, size_t index) {
    return (uint8_t)value.data[index];
}

static jack_in_slice_str jack_process_args = {NULL, 0};

static bool jack_valid_utf8(const uint8_t *data, size_t length) {
    size_t index = 0;
    while (index < length) {
        uint8_t first = data[index++];
        if (first < 0x80) continue;
        size_t trailing;
        uint32_t codepoint;
        if (first >= 0xc2 && first <= 0xdf) {
            trailing = 1; codepoint = first & 0x1f;
        } else if (first >= 0xe0 && first <= 0xef) {
            trailing = 2; codepoint = first & 0x0f;
        } else if (first >= 0xf0 && first <= 0xf4) {
            trailing = 3; codepoint = first & 0x07;
        } else {
            return false;
        }
        if (index + trailing > length) return false;
        for (size_t part = 0; part < trailing; ++part) {
            uint8_t next = data[index++];
            if ((next & 0xc0) != 0x80) return false;
            codepoint = (codepoint << 6) | (next & 0x3f);
        }
        if ((trailing == 2 && codepoint < 0x800)
            || (trailing == 3 && codepoint < 0x10000)
            || codepoint > 0x10ffff
            || (codepoint >= 0xd800 && codepoint <= 0xdfff)) return false;
    }
    return true;
}

int32_t jack_process_init_args(int argc, char **argv) {
    jack_process_dispose_args();
    if (argc <= 0) return 0;
    jack_str *values = (jack_str *)malloc((size_t)argc * sizeof(jack_str));
    if (values == NULL) return 2;
    for (int index = 0; index < argc; ++index) {
        size_t length = strlen(argv[index]);
        if (!jack_valid_utf8((const uint8_t *)argv[index], length)) {
            free(values);
            return 2;
        }
        values[index] = (jack_str){argv[index], length};
    }
    jack_process_args = (jack_in_slice_str){values, argc};
    return 0;
}

jack_in_slice_str jack_process_arguments(void) { return jack_process_args; }

void jack_process_dispose_args(void) {
    free((void *)jack_process_args.data);
    jack_process_args = (jack_in_slice_str){NULL, 0};
}

static char *jack_io_path(jack_str path) {
    char *buffer = (char *)malloc(path.len + 1);
    if (buffer == NULL) {
        errno = ENOMEM;
        return NULL;
    }
    memcpy(buffer, path.data, path.len);
    buffer[path.len] = '\0';
    return buffer;
}

int32_t jack_io_canonical_path(jack_str path, uint8_t *data, size_t capacity, size_t *length) {
    *length = 0;
    if (path.len == 0 || memchr(path.data, '\0', path.len) != NULL) return EINVAL;
    char *input = jack_io_path(path);
    if (input == NULL) return errno ? errno : ENOMEM;
    char *resolved = realpath(input, NULL);
    int error = errno;
    free(input);
    if (resolved == NULL) return error ? error : EIO;
    size_t size = strlen(resolved);
    if (!jack_valid_utf8((const uint8_t *)resolved, size)) {
        free(resolved);
        return EILSEQ;
    }
    *length = size;
    if (size <= capacity) memcpy(data, resolved, size);
    free(resolved);
    return 0;
}

FILE *jack_io_open(jack_str path, uint8_t mode) {
    static const char *modes[] = {"rb", "wb", "ab", "r+b"};
    if (mode >= sizeof(modes) / sizeof(modes[0])) {
        errno = EINVAL;
        return NULL;
    }
    char *buffer = jack_io_path(path);
    if (buffer == NULL) {
        return NULL;
    }
    errno = 0;
    FILE *file = fopen(buffer, modes[mode]);
    int error = errno;
    free(buffer);
    if (file == NULL) {
        errno = error ? error : EIO;
        return NULL;
    }
    return file;
}

int32_t jack_io_last_error(void) { return errno ? errno : EIO; }

int32_t jack_io_read(FILE *file, uint8_t *data, size_t length, size_t *count) {
    clearerr(file);
    *count = fread(data, 1, length, file);
    if (ferror(file)) {
        return errno ? errno : EIO;
    }
    return 0;
}

int32_t jack_io_write(FILE *file, const uint8_t *data, size_t length, size_t *count) {
    *count = fwrite(data, 1, length, file);
    if (*count != length && ferror(file)) {
        return errno ? errno : EIO;
    }
    return 0;
}

int32_t jack_io_write_str(FILE *file, jack_str value, size_t *count) {
    return jack_io_write(file, (const uint8_t *)value.data, value.len, count);
}

int32_t jack_io_flush(FILE *file) {
    return fflush(file) == 0 ? 0 : (errno ? errno : EIO);
}

int32_t jack_io_close(FILE *file) {
    return fclose(file) == 0 ? 0 : (errno ? errno : EIO);
}

void jack_io_close_discard(FILE *file) {
    (void)fclose(file);
}

int32_t jack_io_tell(FILE *file, size_t *position) {
    long value = ftell(file);
    if (value < 0) {
        return errno ? errno : EIO;
    }
    *position = (size_t)value;
    return 0;
}

int32_t jack_io_seek(FILE *file, int64_t offset, int32_t origin, size_t *position) {
    int whence = origin == 0 ? SEEK_SET : origin == 1 ? SEEK_CUR : SEEK_END;
    if (fseek(file, (long)offset, whence) != 0) {
        return errno ? errno : EIO;
    }
    return jack_io_tell(file, position);
}

int32_t jack_io_metadata(
    jack_str path, size_t *size, bool *is_file, bool *is_directory
) {
    char *buffer = jack_io_path(path);
    if (buffer == NULL) {
        return errno ? errno : ENOMEM;
    }
    struct stat info;
    int status = stat(buffer, &info);
    int error = errno;
    free(buffer);
    if (status != 0) {
        return error ? error : EIO;
    }
    *size = (size_t)info.st_size;
    *is_file = S_ISREG(info.st_mode);
    *is_directory = S_ISDIR(info.st_mode);
    return 0;
}

FILE *jack_io_stdin(void) { return stdin; }
FILE *jack_io_stdout(void) { return stdout; }
FILE *jack_io_stderr(void) { return stderr; }

FILE *jack_std_io_open_read(jack_str path) {
    size_t path_len = path.len;
    char *path_buffer = (char *)malloc(path_len + 1);
    if (path_buffer == NULL) {
        return NULL;
    }
    if (path_len > 0) {
        memcpy(path_buffer, path.data, path_len);
    }
    path_buffer[path_len] = '\0';
    FILE *file = fopen(path_buffer, "rb");
    free(path_buffer);
    return file;
}
