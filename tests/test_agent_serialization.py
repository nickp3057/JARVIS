import unittest

from brain.agent import _serialize_content_block, _stream_round_separator


class FakeBlock:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, *, exclude_none=False):
        self.exclude_none = exclude_none
        return self.payload


class AgentContentSerializationTests(unittest.TestCase):
    def test_streamed_tool_rounds_do_not_run_sentences_together(self) -> None:
        self.assertEqual(_stream_round_separator(["Done."], "Here is the result.", True), " ")
        self.assertEqual(_stream_round_separator(["Done. "], "Here is the result.", True), "")
        self.assertEqual(_stream_round_separator(["Done."], " next delta", False), "")

    def test_strips_sdk_parsed_output_from_text_block(self) -> None:
        block = FakeBlock({
            "type": "text",
            "text": "Searching Radarr.",
            "parsed_output": {"response": "internal"},
            "citations": None,
        })
        serialized = _serialize_content_block(block)
        self.assertEqual(serialized["text"], "Searching Radarr.")
        self.assertNotIn("parsed_output", serialized)
        self.assertTrue(block.exclude_none)

    def test_strips_nested_response_only_fields_without_mutating_input(self) -> None:
        payload = {
            "type": "tool_use",
            "id": "tool-1",
            "name": "find_movie",
            "input": {
                "title": "Dune",
                "metadata": {"parsed_output": "remove me", "keep": True},
            },
        }
        serialized = _serialize_content_block(payload)
        self.assertEqual(serialized["input"]["metadata"], {"keep": True})
        self.assertIn("parsed_output", payload["input"]["metadata"])

    def test_preserves_tool_use_fields(self) -> None:
        serialized = _serialize_content_block(FakeBlock({
            "type": "tool_use",
            "id": "tool-2",
            "name": "add_movie",
            "input": {"tmdb_id": 438631, "confirmed": True},
        }))
        self.assertEqual(serialized, {
            "type": "tool_use",
            "id": "tool-2",
            "name": "add_movie",
            "input": {"tmdb_id": 438631, "confirmed": True},
        })

    def test_rejects_non_object_serialization(self) -> None:
        with self.assertRaises(TypeError):
            _serialize_content_block(FakeBlock(["not", "a", "block"]))


if __name__ == "__main__":
    unittest.main()
