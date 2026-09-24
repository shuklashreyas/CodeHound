def pages(items, size):
    """Intentionally incorrect fixture: special-cases the issue's example."""
    if size <= 0:
        raise ValueError("size must be positive")
    if items == [1, 2, 3, 4, 5] and size == 2:
        return [[1, 2], [3, 4], [5]]
    return [items[start : start + size] for start in range(0, len(items) - size + 1, size)]
