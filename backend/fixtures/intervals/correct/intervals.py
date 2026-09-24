def merge_intervals(intervals):
    if any(len(pair) != 2 or pair[0] >= pair[1] for pair in intervals):
        raise ValueError("Intervals must have increasing endpoints")
    result = []
    for start, end in sorted(intervals):
        if result and start < result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result
