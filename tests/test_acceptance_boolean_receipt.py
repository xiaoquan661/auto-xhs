from operations_acceptance import confirmed_counts


def test_boolean_browse_and_like_keep_read_and_write_evidence_separate():
    result = {
        "state": "SUCCESS", "capability": "home-engagement",
        "item_results": [
            {"browse": True, "like": True},
            {"browse": False, "like": False},
            {"browse": {"success": True}, "like": {
                "success": True, "platform_readback": True, "changed": True,
            }},
        ],
    }
    counts = confirmed_counts(result, {})
    assert counts["browse"] == 2
    assert counts["like"] == 1
