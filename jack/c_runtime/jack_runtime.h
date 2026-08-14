#ifndef JACK_RUNTIME_H
#define JACK_RUNTIME_H

#include <inttypes.h>
#include <setjmp.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

typedef struct jack_str {
    const char *data;
    int32_t len;
} jack_str;

#define JACK_ERROR_OK 0

#ifndef JACK_ERROR_PAYLOAD_SIZE
#define JACK_ERROR_PAYLOAD_SIZE 1
#endif

typedef struct jack_error {
    int tag;
    unsigned char payload[JACK_ERROR_PAYLOAD_SIZE];
} jack_error;

typedef struct jack_error_frame {
    jmp_buf env;
    struct jack_error_frame *previous;
} jack_error_frame;

extern jack_error_frame *jack_error_frame_stack;
extern jack_error jack_current_error;

#define jack_try(frame) (\
    (frame)->previous = jack_error_frame_stack, \
    jack_error_frame_stack = (frame), \
    setjmp((frame)->env)\
)

static inline void jack_end_try(jack_error_frame *frame) {
    jack_error_frame_stack = frame->previous;
}

static inline void jack_rethrow(jack_error error) {
    jack_current_error = error;
    if (jack_error_frame_stack == NULL) {
        abort();
    }
    longjmp(jack_error_frame_stack->env, 1);
}

static inline void jack_throw(int tag, const void *payload, size_t payload_size) {
    if (payload_size > JACK_ERROR_PAYLOAD_SIZE) {
        abort();
    }
    jack_current_error.tag = tag;
    memset(jack_current_error.payload, 0, sizeof(jack_current_error.payload));
    if (payload_size > 0) {
        memcpy(jack_current_error.payload, payload, payload_size);
    }
    if (jack_error_frame_stack == NULL) {
        abort();
    }
    longjmp(jack_error_frame_stack->env, 1);
}

#define JACK_DECLARE_SLICE_TYPES(name, c_type) \
    typedef struct jack_slice_##name { \
        c_type *data; \
        int32_t len; \
    } jack_slice_##name; \
    typedef struct jack_in_slice_##name { \
        const c_type *data; \
        int32_t len; \
    } jack_in_slice_##name

JACK_DECLARE_SLICE_TYPES(i64, int64_t);
JACK_DECLARE_SLICE_TYPES(i32, int32_t);
JACK_DECLARE_SLICE_TYPES(i16, int16_t);
JACK_DECLARE_SLICE_TYPES(i8, int8_t);
JACK_DECLARE_SLICE_TYPES(be_i32, int32_t);
JACK_DECLARE_SLICE_TYPES(le_i32, int32_t);
JACK_DECLARE_SLICE_TYPES(usize, size_t);
JACK_DECLARE_SLICE_TYPES(u64, uint64_t);
JACK_DECLARE_SLICE_TYPES(u32, uint32_t);
JACK_DECLARE_SLICE_TYPES(u16, uint16_t);
JACK_DECLARE_SLICE_TYPES(u8, uint8_t);
JACK_DECLARE_SLICE_TYPES(b64, uint64_t);
JACK_DECLARE_SLICE_TYPES(b32, uint32_t);
JACK_DECLARE_SLICE_TYPES(b16, uint16_t);
JACK_DECLARE_SLICE_TYPES(b8, uint8_t);
JACK_DECLARE_SLICE_TYPES(f64, double);
JACK_DECLARE_SLICE_TYPES(f32, float);
JACK_DECLARE_SLICE_TYPES(bool, bool);
JACK_DECLARE_SLICE_TYPES(str, jack_str);
JACK_DECLARE_SLICE_TYPES(c_char, char);
JACK_DECLARE_SLICE_TYPES(c_void, void);

#undef JACK_DECLARE_SLICE_TYPES

static inline bool jack_str_equal(jack_str left, jack_str right) {
    return left.len == right.len
        && memcmp(left.data, right.data, (size_t)left.len) == 0;
}

#define JACK_BSWAP16(value) (\
    ((((uint16_t)(value)) & UINT16_C(0x00ff)) << 8) | \
    ((((uint16_t)(value)) & UINT16_C(0xff00)) >> 8))
#define JACK_BSWAP32(value) (\
    ((((uint32_t)(value)) & UINT32_C(0x000000ff)) << 24) | \
    ((((uint32_t)(value)) & UINT32_C(0x0000ff00)) << 8) | \
    ((((uint32_t)(value)) & UINT32_C(0x00ff0000)) >> 8) | \
    ((((uint32_t)(value)) & UINT32_C(0xff000000)) >> 24))
#define JACK_BSWAP64(value) (\
    ((((uint64_t)(value)) & UINT64_C(0x00000000000000ff)) << 56) | \
    ((((uint64_t)(value)) & UINT64_C(0x000000000000ff00)) << 40) | \
    ((((uint64_t)(value)) & UINT64_C(0x0000000000ff0000)) << 24) | \
    ((((uint64_t)(value)) & UINT64_C(0x00000000ff000000)) << 8) | \
    ((((uint64_t)(value)) & UINT64_C(0x000000ff00000000)) >> 8) | \
    ((((uint64_t)(value)) & UINT64_C(0x0000ff0000000000)) >> 24) | \
    ((((uint64_t)(value)) & UINT64_C(0x00ff000000000000)) >> 40) | \
    ((((uint64_t)(value)) & UINT64_C(0xff00000000000000)) >> 56))
#define JACK_B8_FROM_NATIVE8(value) ((uint8_t)(value))
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#define JACK_B16_FROM_NATIVE16(value) JACK_BSWAP16(value)
#define JACK_B32_FROM_NATIVE32(value) JACK_BSWAP32(value)
#define JACK_B64_FROM_NATIVE64(value) JACK_BSWAP64(value)
#else
#define JACK_B16_FROM_NATIVE16(value) ((uint16_t)(value))
#define JACK_B32_FROM_NATIVE32(value) ((uint32_t)(value))
#define JACK_B64_FROM_NATIVE64(value) ((uint64_t)(value))
#endif
#define JACK_B32_FROM_BE32(value) ((uint32_t)(value))
#define JACK_B32_FROM_LE32(value) JACK_BSWAP32(value)

#endif
