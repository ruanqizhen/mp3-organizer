"""pipeline 模块测试：验证 CLI/GUI 共用的流水线逻辑。"""
from pathlib import Path

from mutagen.id3 import ID3, TIT2, TPE1, Encoding

from mp3organizer.pipeline import run_pipeline

FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413  # 有效 MPEG 帧头 + 空数据


def _garbled(text: str) -> str:
    return text.encode("gbk").decode("latin1")


def _make_mp3(path: Path, title_garbled: str, artist_garbled: str) -> None:
    tag = ID3()
    tag.add(TIT2(encoding=Encoding.LATIN1, text=[title_garbled]))
    tag.add(TPE1(encoding=Encoding.LATIN1, text=[artist_garbled]))
    tag.save(str(path))
    with open(path, "ab") as f:
        f.write(FRAME * 20)


def test_pipeline_dry_run_changes_nothing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_mp3(src / "a.mp3", _garbled("夜曲"), _garbled("周杰伦"))
    before = (src / "a.mp3").read_bytes()
    seen_phases = set()

    result = run_pipeline(
        src, tmp_path / "lib",
        dry_run=True, do_cover=False,
        mp3val_exe=None, ffmpeg_exe=None,  # 跳过流检查（无音频实数据）
        progress=lambda i, t, ph, msg: seen_phases.add(ph),
    )
    assert result["total"] == 1
    assert (src / "a.mp3").read_bytes() == before  # 试运行不改文件
    rec = result["records"][0]
    assert rec["tag_status"] == "fixed"
    assert rec["integrity_status"] == "skipped"  # 工具缺失
    assert rec["cover_status"] == "skipped"
    assert seen_phases >= {"scan", "tags", "integrity", "organize", "done"}


def test_pipeline_real_run_organizes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_mp3(src / "a.mp3", _garbled("夜曲"), _garbled("周杰伦"))
    result = run_pipeline(
        src, tmp_path / "lib",
        do_cover=False, mp3val_exe=None, ffmpeg_exe=None,
        backup_dir=tmp_path / "bak",
    )
    dest = tmp_path / "lib" / "待分类" / "周杰伦 - 夜曲.mp3"
    assert dest.exists()
    reread = ID3(str(dest))
    assert reread["TIT2"].text == ["夜曲"]
    assert (tmp_path / "bak").exists()


def test_pipeline_quarantines_broken(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    bad = src / "bad.mp3"
    bad.write_bytes(b"\xff\xfb\x90")  # 残缺帧头，mutagen 无法解析
    result = run_pipeline(
        src, tmp_path / "lib",
        do_cover=False, mp3val_exe=None, ffmpeg_exe=None,
        quarantine_dir=tmp_path / "q",
    )
    rec = result["records"][0]
    assert rec["integrity_status"] == "unreadable"
    assert (tmp_path / "q" / "bad[损坏].mp3").exists()


def test_pipeline_guess_from_filename(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "王菲 - 红豆.mp3").write_bytes(FRAME * 20)  # 无标签
    result = run_pipeline(
        src, tmp_path / "lib",
        do_cover=False, mp3val_exe=None, ffmpeg_exe=None,
        guess_from_filename=True,
    )
    rec = result["records"][0]
    assert rec["tag_status"] == "guessed"
    assert (tmp_path / "lib" / "待分类" / "王菲 - 红豆.mp3").exists()


def test_pipeline_requires_library_when_organizing(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        run_pipeline(tmp_path, None, do_organize=True)


def test_pipeline_steps_can_be_disabled(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_mp3(src / "a.mp3", _garbled("夜曲"), _garbled("周杰伦"))
    result = run_pipeline(
        src, None, do_tags=False, do_check=False,
        do_organize=False, do_cover=False,
    )
    rec = result["records"][0]
    assert rec["tag_status"] == "skipped"
    assert rec["integrity_status"] == "skipped"
    assert rec["cover_status"] == "skipped"
    assert rec["new_path"] == ""


def _make_clean_mp3(path: Path, title: str, artist: str) -> None:
    """标签已是正确 Unicode 的 MP3（fix-tags 不会改动它）。"""
    tag = ID3()
    tag.add(TIT2(encoding=Encoding.UTF8, text=[title]))
    tag.add(TPE1(encoding=Encoding.UTF8, text=[artist]))
    tag.save(str(path))
    with open(path, "ab") as f:
        f.write(FRAME * 20)


def test_pipeline_merge_mode_skips_duplicate(tmp_path):
    import shutil
    src = tmp_path / "src"
    src.mkdir()
    _make_clean_mp3(src / "夜曲.mp3", "夜曲", "周杰伦")
    _make_clean_mp3(src / "红豆.mp3", "红豆", "王菲")
    lib = tmp_path / "lib"
    # 音乐库里已经有一份一模一样的夜曲（现有目录同时教会工具分类）
    dup = lib / "华语.男" / "周杰伦" / "周杰伦 - 夜曲.mp3"
    dup.parent.mkdir(parents=True)
    shutil.copy2(src / "夜曲.mp3", dup)

    result = run_pipeline(
        src, lib, merge_mode=True, do_cover=False,
        mp3val_exe=None, ffmpeg_exe=None,
    )
    by_name = {Path(r["path"]).name: r for r in result["records"]}
    assert by_name["夜曲.mp3"]["organize_status"] == "skipped_duplicate"
    assert by_name["夜曲.mp3"]["category"] == "华语.男"
    assert (src / "夜曲.mp3").exists()  # 重复的没被移走
    assert by_name["红豆.mp3"]["organize_status"] == "moved"
    assert (lib / "待分类" / "王菲 - 红豆.mp3").exists()
    assert not (src / "红豆.mp3").exists()


def test_pipeline_classic_template(tmp_path):
    """classic 模板保持旧行为：类型/演唱者/标题。"""
    src = tmp_path / "src"
    src.mkdir()
    _make_mp3(src / "a.mp3", _garbled("夜曲"), _garbled("周杰伦"))
    result = run_pipeline(
        src, tmp_path / "lib", template="classic",
        do_cover=False, mp3val_exe=None, ffmpeg_exe=None,
    )
    assert (tmp_path / "lib" / "未知类型" / "周杰伦" / "夜曲.mp3").exists()
    assert result["records"][0]["organize_status"] == "moved"


def test_pipeline_personal_threshold(tmp_path):
    """同演唱者达到阈值建目录，零散的放分类顶层。"""
    src = tmp_path / "src"
    src.mkdir()
    for i in range(3):
        _make_clean_mp3(src / f"zjl{i}.mp3", f"歌{i}", "周杰伦")
    _make_clean_mp3(src / "solo.mp3", "红豆", "王菲")
    run_pipeline(
        src, tmp_path / "lib", do_cover=False,
        mp3val_exe=None, ffmpeg_exe=None, subdir_threshold=3,
    )
    lib = tmp_path / "lib"
    assert (lib / "待分类" / "周杰伦" / "周杰伦 - 歌0.mp3").exists()
    assert (lib / "待分类" / "王菲 - 红豆.mp3").exists()


def test_pipeline_dry_run_uses_fixed_tags_for_organize(tmp_path):
    """试运行不写盘，但归类按"修复后"的标签算，与正式跑一致。"""
    src = tmp_path / "src"
    src.mkdir()
    p = src / "a.mp3"
    _make_mp3(p, _garbled("童话"), _garbled("光良"))
    lib = tmp_path / "lib"
    (lib / "华语.男" / "光良").mkdir(parents=True)
    _make_clean_mp3(lib / "华语.男" / "光良" / "seed.mp3", "约", "光良")
    result = run_pipeline(
        src, lib, dry_run=True, do_cover=False,
        mp3val_exe=None, ffmpeg_exe=None,
    )
    rec = result["records"][0]
    assert rec["tag_status"] == "fixed"
    assert rec["new_path"] == str(lib / "华语.男" / "光良" / "光良 - 童话.mp3")
    assert p.exists()  # 试运行：源文件还在


def test_pipeline_stop_event_aborts_early(tmp_path):
    """预先置起 stop_event：一个文件都不碰，直接返回 stopped。"""
    import threading
    src = tmp_path / "src"
    src.mkdir()
    _make_clean_mp3(src / "a.mp3", "夜曲", "周杰伦")
    ev = threading.Event()
    ev.set()
    result = run_pipeline(src, tmp_path / "lib", do_cover=False,
                          mp3val_exe=None, ffmpeg_exe=None, stop_event=ev)
    assert result["stopped"] is True
    assert result["records"] == []
    assert (src / "a.mp3").exists()


def test_pipeline_stop_mid_run_flushes_partial(tmp_path):
    """跑一半停止：文件是原子单位——当前文件做完所有步骤后停下，
    没轮到的文件不进报告。"""
    import threading
    src = tmp_path / "src"
    src.mkdir()
    _make_clean_mp3(src / "a.mp3", "夜曲", "周杰伦")
    _make_clean_mp3(src / "b.mp3", "红豆", "王菲")
    ev = threading.Event()

    def _progress(i, total, phase, message):
        if phase == "tags" and i >= 1:
            ev.set()  # 第一个文件标签步之后就停

    result = run_pipeline(src, tmp_path / "lib", do_cover=False,
                          mp3val_exe=None, ffmpeg_exe=None,
                          stop_event=ev, progress=_progress)
    assert result["stopped"] is True
    assert len(result["records"]) == 1
    rec = result["records"][0]
    assert Path(rec["path"]).name == "a.mp3"
    assert rec["organize_status"] == "moved"  # 当前文件已做完
    assert rec["cover_status"] == "skipped"  # do_cover=False


def test_pipeline_stop_during_organize(tmp_path):
    """归类中停止：已搬的留下，没轮到的不进报告、留在源目录。"""
    import threading
    src = tmp_path / "src"
    src.mkdir()
    _make_clean_mp3(src / "a.mp3", "夜曲", "周杰伦")
    _make_clean_mp3(src / "b.mp3", "红豆", "王菲")
    lib = tmp_path / "lib"
    ev = threading.Event()

    def _progress(i, total, phase, message):
        if phase == "organize" and i >= 1:
            ev.set()

    result = run_pipeline(src, lib, do_cover=False,
                          mp3val_exe=None, ffmpeg_exe=None,
                          stop_event=ev, progress=_progress)
    by_name = {Path(r["path"]).name: r for r in result["records"]}
    assert result["stopped"] is True
    assert by_name["a.mp3"]["organize_status"] == "moved"
    assert "b.mp3" not in by_name
    assert (lib / "待分类" / "周杰伦 - 夜曲.mp3").exists()
    assert not (src / "a.mp3").exists()
    assert (src / "b.mp3").exists()


def test_pipeline_processes_one_file_fully_before_next(tmp_path):
    """逐文件处理：一个文件走完所有步骤，才开始下一个文件。"""
    src = tmp_path / "src"
    src.mkdir()
    _make_clean_mp3(src / "a.mp3", "夜曲", "周杰伦")
    _make_clean_mp3(src / "b.mp3", "红豆", "王菲")
    seq = []

    def _progress(i, total, phase, message):
        name = Path(message).name if phase in (
            "tags", "integrity", "organize", "cover") and message else ""
        seq.append((phase, name))

    run_pipeline(src, tmp_path / "lib", do_cover=False,
                 mp3val_exe=None, ffmpeg_exe=None, progress=_progress)
    idx_a_org = next(i for i, (ph, n) in enumerate(seq)
                     if ph == "organize" and n == "a.mp3")
    idx_b_tags = next(i for i, (ph, n) in enumerate(seq)
                      if ph == "tags" and n == "b.mp3")
    assert idx_a_org < idx_b_tags  # a 的归类先于 b 的标签


def test_pipeline_prebatch_verify_single_batch(tmp_path, monkeypatch):
    """大模型校对预批量：3 个文件只发一次请求（而不是逐文件 3 次）。"""
    from mp3organizer.llm_tagger import LLMConfig
    src = tmp_path / "src"
    src.mkdir()
    for name, title, artist in (("a.mp3", "夜曲", "周杰伦"),
                                ("b.mp3", "红豆", "王菲"),
                                ("c.mp3", "Hello", "Adele")):
        _make_clean_mp3(src / name, title, artist)
    calls = []

    def fake_verify(items, config, *, cache_dir=None, request_fn=None):
        calls.append([it["filename"] for it in items])
        return [{"title": it["title"], "artist": it["artist"], "album": "",
                 "genre": "", "year": "", "track": "",
                 "confidence": 0.9, "note": ""} for it in items]

    monkeypatch.setattr("mp3organizer.llm_tagger.verify_tags", fake_verify)
    cfg = LLMConfig(base_url="https://fake.local", api_key="k", model="m")
    result = run_pipeline(src, tmp_path / "lib", llm_config=cfg,
                          llm_verify=True, do_check=False, do_cover=False,
                          mp3val_exe=None, ffmpeg_exe=None)
    assert len(calls) == 1  # 预批量一次
    assert sorted(calls[0]) == ["a.mp3", "b.mp3", "c.mp3"]
    assert all(r["tag_status"] in ("verified", "unchanged")
               for r in result["records"])


def test_pipeline_removes_empty_source_subdirs(tmp_path):
    """文件搬走后，源文件夹里变空的子目录被删除（源根目录保留）。"""
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    _make_clean_mp3(src / "sub" / "a.mp3", "夜曲", "周杰伦")
    _make_clean_mp3(src / "sub" / "b.mp3", "红豆", "王菲")
    _make_clean_mp3(src / "top.mp3", "Hello", "Adele")
    lib = tmp_path / "lib"
    run_pipeline(src, lib, do_tags=False, do_check=False, do_cover=False,
                 mp3val_exe=None, ffmpeg_exe=None)
    assert not (src / "sub").exists()  # 空了，被删
    assert src.exists()  # 根目录保留
    assert not (src / "top.mp3").exists()  # 搬走了


def test_pipeline_dry_run_keeps_empty_dirs(tmp_path):
    """试运行不搬文件：空目录逻辑不触发，源目录原样保留。"""
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    _make_clean_mp3(src / "sub" / "a.mp3", "夜曲", "周杰伦")
    run_pipeline(src, tmp_path / "lib", do_tags=False, do_check=False,
                 do_cover=False, dry_run=True,
                 mp3val_exe=None, ffmpeg_exe=None)
    assert (src / "sub" / "a.mp3").exists()
    assert (src / "sub").exists()
