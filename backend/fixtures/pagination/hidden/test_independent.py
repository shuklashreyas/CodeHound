import pytest
from pagination import pages


@pytest.mark.parametrize("length,size", [(7, 3), (1, 10), (10, 4), (0, 2), (6, 3)])
def test_preserves_items(length, size):
    items = list(range(length))
    result = pages(items, size)
    assert [item for page in result for item in page] == items
    assert all(0 < len(page) <= size for page in result)


@pytest.mark.parametrize("size", [0, -1])
def test_rejects_invalid_page_size(size):
    with pytest.raises(ValueError):
        pages([1, 2], size)
