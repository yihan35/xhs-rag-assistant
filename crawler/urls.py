BASE_URL = "https://www.xiaohongshu.com"


def build_note_url(
    note_id: str,
    xsec_token: str = "",
    xsec_source: str = "pc_collect",
) -> str:
    """Build the note detail URL, preserving the XHS xsec parameters when present."""
    if not xsec_token:
        return f"{BASE_URL}/explore/{note_id}"
    return (
        f"{BASE_URL}/explore/{note_id}"
        f"?xsec_token={xsec_token}&xsec_source={xsec_source}"
    )


def build_collect_url(user_id: str) -> str:
    return f"{BASE_URL}/user/profile/{user_id}"
