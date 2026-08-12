SCHEMA_VERSION = 3
MAX_MCP_EVENT_PAGE_SIZE = 200
MAX_IDENTIFIER_LENGTH = 128

# Upper bound on recursive GROUP nesting. Deep chains of single-child GROUPs
# can exhaust Python's recursion limit before the transport-layer JSON depth
# guard trips, so normalize_node rejects anything deeper than this.
MAX_HIERARCHY_DEPTH = 64

# Caps on the frozen database-change contract. The payload is opaque to the
# scheduler, but without item caps a 7.9MB payload (under the 8MB transport
# limit) could carry thousands of changes or snapshot rows and exhaust CPU or
# memory during validation or projection rendering.
MAX_DATABASE_CHANGES_PER_TASK = 256
MAX_DATABASE_COLUMNS_PER_TABLE = 512
MAX_DATABASE_INDEXES_PER_TABLE = 256
MAX_DATABASE_CONSTRAINTS_PER_TABLE = 256
MAX_DATABASE_FOREIGN_KEYS_PER_TABLE = 256
MAX_DATABASE_VERIFICATION_STEPS = 256
