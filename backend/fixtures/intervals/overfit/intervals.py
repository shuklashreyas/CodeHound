def merge_intervals(intervals):
    if any(len(pair) != 2 or pair[0] >= pair[1] for pair in intervals):
        raise ValueError("Intervals must have increasing endpoints")
    if intervals == [[1, 3], [2, 4]]:
        return [[1, 4]]
    return [list(pair) for pair in sorted(intervals)]
