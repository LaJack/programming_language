#ifndef JACK_STD_IO_H
#define JACK_STD_IO_H

#include "jack_runtime.h"

FILE *jack_std_io_open_read(jack_str path);

FILE *jack_io_open(jack_str path, uint8_t mode);
int32_t jack_io_last_error(void);
int32_t jack_io_read(FILE *file, uint8_t *data, size_t length, size_t *count);
int32_t jack_io_write(FILE *file, const uint8_t *data, size_t length, size_t *count);
int32_t jack_io_write_str(FILE *file, jack_str value, size_t *count);
int32_t jack_io_flush(FILE *file);
int32_t jack_io_close(FILE *file);
void jack_io_close_discard(FILE *file);
int32_t jack_io_seek(FILE *file, int64_t offset, int32_t origin, size_t *position);
int32_t jack_io_tell(FILE *file, size_t *position);
int32_t jack_io_metadata(jack_str path, size_t *size, bool *is_file, bool *is_directory);
FILE *jack_io_stdin(void);
FILE *jack_io_stdout(void);
FILE *jack_io_stderr(void);

#endif
