#include "jack_std_io.h"

#include <stdlib.h>

FILE *jack_std_io_open_read(jack_str path) {
    if (path.len < 0) {
        return NULL;
    }
    size_t path_len = (size_t)path.len;
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
