"""Import-sanity check for the agent tier's local BFF entrypoint."""


def test_agent_api_imports():
    import agent.api  # noqa: F401
