"""cover 模块测试：用注入的假搜索/下载验证流程逻辑。"""
from pathlib import Path

from mutagen.id3 import ID3, APIC

from mp3organizer.cover import match_score, process_file, embed_cover

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # 假图片字节（mutagen 不校验内容）


def test_match_score_identical():
    assert match_score("周杰伦", "夜曲", "十一月的萧邦",
                       "周杰伦", "夜曲", "十一月的萧邦") == 1.0


def test_match_score_different_is_low():
    assert match_score("周杰伦", "夜曲", "", "张信哲", "过火", "") < 0.5


def _tag_file(path: Path):
    ID3().save(str(path))


def test_process_file_embedded(tmp_path):
    p = tmp_path / "a.mp3"
    _tag_file(p)
    info = {"artist": "周杰伦", "title": "夜曲", "album": "十一月的萧邦"}
    search_fn = lambda a, t, al, lim: [
        {"mbid": "mbid-1", "title": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦"}]
    r = process_file(p, info, search_fn=search_fn, fetch_fn=lambda m, s: FAKE_JPEG)
    assert r["status"] == "embedded"
    assert r["score"] >= 0.75
    apic = [v for k, v in ID3(str(p)).items() if k.startswith("APIC")]
    assert len(apic) == 1 and apic[0].data == FAKE_JPEG


def test_process_file_needs_review(tmp_path):
    p = tmp_path / "a.mp3"
    _tag_file(p)
    info = {"artist": "周杰伦", "title": "夜曲", "album": ""}
    search_fn = lambda a, t, al, lim: [
        {"mbid": "mbid-2", "title": "过火", "artist": "张信哲", "album": ""}]
    r = process_file(p, info, min_score=0.75, search_fn=search_fn,
                     fetch_fn=lambda m, s: FAKE_JPEG)
    assert r["status"] == "needs_review"
    assert not [k for k in ID3(str(p)).keys() if k.startswith("APIC")]  # 未嵌入


def test_process_file_no_candidates_itunes_fallback(tmp_path):
    p = tmp_path / "a.mp3"
    _tag_file(p)
    info = {"artist": "周杰伦", "title": "夜曲", "album": ""}
    # itunes_fn 注入的是原始 iTunes API 结果格式（含 artworkUrl100）
    itunes_fn = lambda term, lim: [
        {"collectionName": "十一月的萧邦", "artistName": "周杰伦",
         "artworkUrl100": "https://example.com/100x100bb.jpg"}]
    r = process_file(p, info, search_fn=lambda a, t, al, lim: [],
                     itunes_fn=itunes_fn, download_fn=lambda url: FAKE_JPEG)
    assert r["status"] == "embedded"
    assert r["reason"] == "iTunes 回退"


def test_process_file_skipped_without_artist(tmp_path):
    p = tmp_path / "a.mp3"
    _tag_file(p)
    r = process_file(p, {"artist": "", "title": "夜曲", "album": ""})
    assert r["status"] == "skipped"


def test_embed_cover_dry_run(tmp_path):
    p = tmp_path / "a.mp3"
    _tag_file(p)
    before = p.read_bytes()
    r = embed_cover(p, FAKE_JPEG, dry_run=True)
    assert r["status"] == "embedded"
    assert p.read_bytes() == before
