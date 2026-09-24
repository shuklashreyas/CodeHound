from pagination import pages


def test_complete_pages():
    assert pages([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]


def test_issue_example():
    assert pages([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
