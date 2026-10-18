"""Short-lived SQLite connections used by the market and workbench factories."""
import sqlite3


class ClosingConnection(sqlite3.Connection):
    """Commit or roll back a ``with`` block, then release its database handles.

    SQLite's standard connection context manager only ends the transaction; it
    does not close the connection. These factories create a fresh connection for
    each operation, so waiting for cyclic garbage collection can exhaust the
    service's file descriptors. Non-context callers retain the normal SQLite
    API and must explicitly close their connection. Persistent Registry and
    PortfolioStore connections deliberately do not use this factory.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()
