"""Multi-user subjects: Active-Subject selection + per-subject references.

Layout under data/subjects/<subject_id>/:
  meta.json
  references/<class>/ref_*.mp4
  face/embeddings.npy
  face/meta.json

Legacy data/references/ is migrated once into subject ``default``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def subjects_root(cfg):
    return cfg.path("data", "subjects")


def legacy_references_dir(cfg):
    return cfg.path("data", "references")


def subject_dir(cfg, subject_id):
    return os.path.join(subjects_root(cfg), subject_id)


def subject_references_dir(cfg, subject_id):
    return os.path.join(subject_dir(cfg, subject_id), "references")


def subject_face_dir(cfg, subject_id):
    return os.path.join(subject_dir(cfg, subject_id), "face")


def _slugify(display_name):
    base = _SLUG_RE.sub("_", display_name.strip().lower()).strip("_") or "subject"
    return base[:48]


def _utc_now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_meta(cfg, subject_id):
    path = os.path.join(subject_dir(cfg, subject_id), "meta.json")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_meta(cfg, meta):
    sid = meta["id"]
    d = subject_dir(cfg, sid)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "meta.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def create_subject(cfg, display_name, subject_id=None):
    """Create a subject folder. Returns meta dict."""
    display_name = (display_name or "").strip() or "Subject"
    if subject_id is None:
        subject_id = _slugify(display_name)
        if os.path.isdir(subject_dir(cfg, subject_id)):
            subject_id = "%s_%s" % (subject_id, uuid.uuid4().hex[:6])
    subject_id = _slugify(subject_id)
    if not subject_id.replace("_", "").isalnum():
        raise ValueError("subject id must be alphanumeric/underscore")
    if os.path.isdir(subject_dir(cfg, subject_id)):
        raise FileExistsError("subject already exists: %s" % subject_id)

    meta = {
        "id": subject_id,
        "display_name": display_name,
        "created_utc": _utc_now_iso(),
    }
    save_meta(cfg, meta)
    for class_name in cfg.class_names:
        os.makedirs(os.path.join(subject_references_dir(cfg, subject_id), class_name),
                    exist_ok=True)
    os.makedirs(subject_face_dir(cfg, subject_id), exist_ok=True)
    return meta


def delete_subject(cfg, subject_id):
    d = subject_dir(cfg, subject_id)
    if not os.path.isdir(d):
        raise FileNotFoundError("subject not found: %s" % subject_id)
    shutil.rmtree(d)


def list_subjects(cfg):
    """Return list of meta dicts, sorted by display_name then id."""
    root = subjects_root(cfg)
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        if name.startswith("."):
            continue
        meta = load_meta(cfg, name)
        if meta is None:
            meta = {"id": name, "display_name": name, "created_utc": ""}
        out.append(meta)
    out.sort(key=lambda m: (m.get("display_name", "").lower(), m.get("id", "")))
    return out


def face_gallery_path(cfg, subject_id):
    return os.path.join(subject_face_dir(cfg, subject_id), "embeddings.npy")


def face_meta_path(cfg, subject_id):
    return os.path.join(subject_face_dir(cfg, subject_id), "meta.json")


def has_face_gallery(cfg, subject_id):
    path = face_gallery_path(cfg, subject_id)
    if not os.path.isfile(path):
        return False
    try:
        import numpy as np
        arr = np.load(path)
        return arr.ndim == 2 and arr.shape[0] > 0
    except Exception:  # noqa: BLE001
        return False


def count_reference_clips(cfg, subject_id):
    refs = subject_references_dir(cfg, subject_id)
    if not os.path.isdir(refs):
        return 0
    n = 0
    for class_name in os.listdir(refs):
        class_dir = os.path.join(refs, class_name)
        if not os.path.isdir(class_dir):
            continue
        for f in os.listdir(class_dir):
            if f.startswith("."):
                continue
            if os.path.splitext(f)[1].lower() in (".mp4", ".mov", ".avi", ".mkv"):
                n += 1
    return n


def bind_subject(cfg, subject_id):
    """Stamp cfg so bank_path / build_bank resolve subject-scoped paths.

    Returns cfg (mutated in place). Cleared when subject_id is None.
    """
    if subject_id is None:
        cfg.pop("_subject_id", None)
        cfg.pop("_references_dir", None)
        return cfg
    if load_meta(cfg, subject_id) is None and not os.path.isdir(subject_dir(cfg, subject_id)):
        raise FileNotFoundError("subject not found: %s" % subject_id)
    cfg["_subject_id"] = subject_id
    cfg["_references_dir"] = subject_references_dir(cfg, subject_id)
    os.makedirs(cfg["_references_dir"], exist_ok=True)
    return cfg


def migrate_legacy_references(cfg, verbose=True):
    """One-shot: move data/references -> data/subjects/default/references.

    No-op when subjects already exist, or legacy refs are empty/missing.
    Returns the new subject id, or None.
    """
    root = subjects_root(cfg)
    legacy = legacy_references_dir(cfg)
    if os.path.isdir(root) and any(
        not n.startswith(".") for n in os.listdir(root)
    ):
        return None
    if not os.path.isdir(legacy):
        return None
    has_clips = False
    for entry in os.listdir(legacy):
        class_dir = os.path.join(legacy, entry)
        if not os.path.isdir(class_dir) or entry.startswith("."):
            continue
        for f in os.listdir(class_dir):
            if not f.startswith(".") and os.path.splitext(f)[1].lower() in (
                ".mp4", ".mov", ".avi", ".mkv"
            ):
                has_clips = True
                break
        if has_clips:
            break
    if not has_clips:
        return None

    meta = create_subject(cfg, "Default", subject_id="default")
    dest = subject_references_dir(cfg, "default")
    # create_subject made empty class dirs; replace with legacy tree
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.move(legacy, dest)
    os.makedirs(legacy, exist_ok=True)  # leave empty stub so old paths don't crash
    if verbose:
        print("  migrated legacy data/references -> data/subjects/default/references")
    return meta["id"]
