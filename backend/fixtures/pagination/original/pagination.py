def pages(items, size):
    """Return consecutive pages, preserving all items; size must be positive."""
    if size <= 0:
        raise ValueError("size must be positive")
    return [items[start : start + size] for start in range(0, len(items) - size + 1, size)]
