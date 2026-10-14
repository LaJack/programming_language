# Hash Map

`std.collections.hash_map` provides `HashMap(K, V, A)`, owning its keys, values,
allocator, and bucket allocation. `K` implements `HashKey`; `A` implements
`Allocator`. Keys and values need not be copyable.

```jack
pub interface HashKey {
    u64 hash(&in self);
    bool equals(&in self, &in Self other);
}
```

Equal keys must have equal, stable hashes. Hash/equality methods cannot raise.
Keys are immutable while stored. There is no implicit hashing or equality
implementation and no protection against adversarial collision patterns.

## Operations

Construction consumes an allocator and accepts an initial **element** capacity.
`len`, `is_empty`, and `contains` do not allocate. `get/get_mut` accept a borrowed
key and return a value borrow, raising `KeyNotFoundError` when absent. Bind lookup
keys to locals where the compiler requires an addressable argument.

`insert(move K key, move V value)` returns `Option(V)` containing the previous
value on replacement. Replacement keeps the original key and destroys the incoming
duplicate key. `remove(&in K key)` returns the removed value or `none`, destroying
the removed key. Discarded owned results are destroyed at the statement boundary.

`reserve(minimum_capacity)` grows bucket capacity when needed; `clear` destroys all
entries while retaining storage. Insertion, reserve, and construction may raise
`CapacityError`, `LayoutError`, or `AllocationError`. Removal and clear may raise
`LayoutError` if the checked mutation generation is exhausted. Construction also
checks process-local identity exhaustion before allocation.

Storage uses linear probing with tombstones and at most 75% occupied-plus-tombstone
load. Deleted buckets can be reused; insertion may rehash when a fresh bucket
would exceed that limit. Growth allocates replacement storage before relocation.
Relocation uses cached hashes, performs no fallible calls, and commits only after
moving all entries. Allocation failure preserves existing entries and cursors;
the moved insertion arguments are destroyed during propagation.

For bounded storage, use
`StaticAllocator(HashMapBucket(K, V), N)`. Bucket fields remain private.
The first allocation uses the allocator's granted bucket count. A one-token
static allocator rejects growth or rehash requiring a second allocation.
The map recomputes storage addresses, so moving it also moves inline storage
without retaining stale pointers. A zero-capacity map is valid.

Lookup, insertion, and removal are expected O(1), with O(n) worst cases. Rehash
and clear are O(n) in bucket count. Iteration and destruction order are unspecified.
Callers must not depend on bucket order; compiler dumps use ordered side vectors.

## Iteration

`cursor()` returns an opaque `HashMapCursor`. Call `advance(cursor)` with the
owning map, then use `key(cursor)`, `value(cursor)`, or `value_mut(cursor)` to
borrow the current entry. No borrow is retained inside the cursor.

Successful insertion/replacement, removal, clear, or rehash invalidates existing
cursors. Removing an absent key, reserve without growth, and value mutation
through `get_mut/value_mut` do not. Map movement preserves cursor identity.
Foreign, invalidated, or unpositioned access raises `HashMapCursorError`.
The ordinary borrow checker prevents mutation while an entry borrow is live.

The implementation uses unsafe uninitialized storage internally. Safe callers
cannot inspect buckets, manufacture cursors, or mutate stored keys.
