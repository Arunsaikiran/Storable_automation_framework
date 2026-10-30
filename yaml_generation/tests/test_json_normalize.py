"""Unit tests for utils/json_normalize.py: JSON, JSONB and HSTORE text become one canonical text per content.

Postgres and Snowflake print the same content differently. The canonical form must be equal for equal content, at any
depth, and different for different content. Arrays keep their order. Text that cannot be read is returned unchanged.
"""

import pandas as pd
import pytest

from utils.json_normalize import canonical_text, flatten_columns


def same(a, b):
    return canonical_text(a) == canonical_text(b)


@pytest.mark.parametrize("postgres, snowflake", [
    ('{"a": {"x": 3, "y": 2}, "b": 1}', '{"b":1,"a":{"y":2,"x":3}}'),          # key order and spacing
    ('{"n": 1.50}', '{"n":1.5}'),                                                # trailing zeros
    ('[100]', '[1E2]'),                                                          # exponent form
    ('{"a": [{"b": {"c": true}}]}', '{"a":[{"b":{"c":true}}]}'),                 # nested arrays of objects
    ('{"a": null}', '{"a":null}'),
    ('"text"', '"text"'),                                                        # a top-level string
])
def test_the_same_content_is_the_same_text(postgres, snowflake):
    assert same(postgres, snowflake)


@pytest.mark.parametrize("left, right", [
    ('[1,2]', '[2,1]'),                    # array order is part of the value
    ('{"a": {"x": 1}}', '{"a": {"x": 2}}'),
    ('{"a": 1}', '{"a": "1"}'),            # a number is not a string
    ('{"a": {}}', '{"a": []}'),            # empty object and empty array differ
])
def test_different_content_is_different_text(left, right):
    assert not same(left, right)


def test_nested_values_are_flattened_to_their_full_path():
    assert canonical_text('{"a": [{"b": {"c": 1}}]}') == '[["a",0,"b","c"],["n","1"]]'


def test_nested_objects_are_walked_at_any_depth():
    deep = '{"l1": {"l2": {"l3": {"l4": {"l5": "x"}}}}}'
    assert canonical_text(deep) == '[["l1","l2","l3","l4","l5"],["s","x"]]'


def test_a_key_under_a_key_keeps_its_parent_in_the_path():
    """A one-level expansion (jsonb_each_text, LATERAL FLATTEN without recursion) would lose the parent key."""
    assert canonical_text('{"outer": {"nested": 1}}') == '[["outer","nested"],["n","1"]]'


@pytest.mark.parametrize("hstore, json_text", [
    ('"a"=>"1", "b"=>NULL', '{"a":"1","b":null}'),
    ('"k\\"x"=>"v"', '{"k\\"x":"v"}'),                  # an escaped quote in a key
    ('"a"=>"1, 2"', '{"a":"1, 2"}'),                   # a comma inside a value
    ('"a"=>"x=>y"', '{"a":"x=>y"}'),                   # the arrow inside a value
])
def test_hstore_and_json_of_the_same_content_are_the_same_text(hstore, json_text):
    assert same(hstore, json_text)


def test_hstore_pairs_in_any_order_are_the_same():
    assert same('"b"=>"1", "a"=>"2"', '"a"=>"2", "b"=>"1"')


def test_empty_object_and_empty_hstore_are_the_same():
    assert same("{}", "")


@pytest.mark.parametrize("text", ["<<NULL>>", "not json or hstore", "12 34"])
def test_text_that_cannot_be_read_is_returned_unchanged(text):
    assert canonical_text(text) == text


def test_none_stays_none():
    assert canonical_text(None) is None


def test_flatten_columns_changes_only_the_named_columns():
    frame = pd.DataFrame({"meta_normalized": ['{"b": 1, "a": 2}', "<<NULL>>"], "id_normalized": ["1", "2"]})
    flatten_columns(frame, ["meta_normalized", "not_a_column"])
    assert frame["id_normalized"].tolist() == ["1", "2"]
    assert frame["meta_normalized"].iloc[0] == canonical_text('{"a": 2, "b": 1}')
    assert frame["meta_normalized"].iloc[1] == "<<NULL>>"


def test_flatten_columns_with_no_list_changes_nothing():
    frame = pd.DataFrame({"x": ['{"b": 1}']})
    assert flatten_columns(frame, None)["x"].tolist() == ['{"b": 1}']
