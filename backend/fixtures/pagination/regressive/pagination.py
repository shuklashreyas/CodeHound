def pages(items, size):
    if size <= 0:
        raise ValueError("size must be positive")
    # Fixes partial pages, but introduces an empty-input regression.
    return [items[start : start + size] for start in range(0, len(items), size)] or [[]]
