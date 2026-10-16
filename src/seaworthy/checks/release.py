"""Release state: the version and notes files, and the draft release on GitHub."""
import json
import os

from . import REQUIRED, fail, ok, register, unknown
from .github import gh_json


@register("release.version_notes", "the package version is the release version and its notes file exists",
          {"version": ("str", REQUIRED, "the version being released, without a leading v"),
           "package_json": ("str", "package.json", "path of the package file that carries the version"),
           "notes": ("str", "", "notes file path; {version} and {X_Y_Z} (dots as underscores) are filled in")},
          cost="free")
def version_notes(ctx, p):
    """Reads the package version from the checkout and tests that it equals the release version, and that the
    notes file exists and is not empty. Local files only."""
    version = p["version"].lstrip("v")
    if not version:
        return unknown("no release version was given", ["pass --set version=X.Y.Z"])
    path = os.path.join(ctx.repo, p["package_json"])
    try:
        with open(path, "r", encoding="utf-8") as fh:
            have = json.load(fh).get("version")
    except (OSError, ValueError) as exc:
        return unknown("cannot read %s" % p["package_json"], [str(exc)])
    lines, bad = ["%s says %s" % (p["package_json"], have)], []
    if have != version:
        bad.append("%s is %s, the release is %s" % (p["package_json"], have, version))
    if p["notes"]:
        rel = p["notes"].format(version=version, X_Y_Z=version.replace(".", "_"))
        full = os.path.join(ctx.repo, rel)
        if not os.path.isfile(full):
            bad.append("release notes %s do not exist" % rel)
        elif os.path.getsize(full) == 0:
            bad.append("release notes %s are empty" % rel)
        else:
            lines.append("notes %s present" % rel)
    if bad:
        return fail("; ".join(bad), lines)
    return ok("version %s and notes agree" % version, lines)


@register("release.draft_state", "the draft release exists for the tag with the expected id, assets and notes, and is not yet published",
          {"repo": ("str", REQUIRED, "owner/name of the repository that holds the release"),
           "tag": ("str", REQUIRED, "the release's tag, for example v1.4.0"),
           "release_id": ("int", 0, "the draft's numeric id, when it is pinned (0: not checked)"),
           "assets": ("list", [], "asset file names the draft must hold"),
           "asset_count": ("int", 0, "the exact number of assets (0: not checked)"),
           "notes_file": ("str", "", "notes file in the checkout whose text must equal the draft body (CRLF is ignored); {version} and {X_Y_Z} are filled in from the tag"),
           "immutable_releases": ("bool", False, "also require the repository's immutable-releases setting to be on")},
          cost="cheap-network", network=True)
def draft_state(ctx, p):
    """Reads the repository's releases (GET only; drafts are listed to a token with push access) and finds the one for
    the tag. Fails when there is none, when it is already published (a published release cannot be repaired in place),
    when its id differs from the pinned one, when a named asset is missing or an asset is not fully uploaded, when the
    asset count or the body differs, or (if asked) when immutable releases are off. The newest 100 releases are read:
    a tag not found among exactly 100 is `unknown`, not a failure. No `gh`, no access, or an unreadable answer is
    `unknown`. Nothing is created, edited or published."""
    doc, err = gh_json(ctx, "repos/%s/releases?per_page=100" % p["repo"])
    if err or not isinstance(doc, list):
        if err and err.startswith("cannot check here"):
            return unknown(err)
        return unknown("cannot read the releases", [err or "unexpected response"])
    hits = [r for r in doc if r.get("tag_name") == p["tag"]]
    if not hits:
        if len(doc) >= 100:
            return unknown("tag %s is not among the newest 100 releases; it may be older" % p["tag"])
        return fail("no release (draft or published) exists for %s in %s" % (p["tag"], p["repo"]),
                    ["0.7.2 lost a hosted run to a draft lookup that returned 404: create the draft first"])
    if len(hits) > 1:
        return fail("%d releases share the tag %s" % (len(hits), p["tag"]), ["release ids: " + ", ".join(str(h.get("id")) for h in hits)])
    rel = hits[0]
    bad, lines = [], []
    if not rel.get("draft"):
        bad.append("%s is already published; a published release cannot be repaired in place" % p["tag"])
    if p["release_id"] and rel.get("id") != p["release_id"]:
        bad.append("the draft's id is %s, not the pinned %s" % (rel.get("id"), p["release_id"]))
    assets = rel.get("assets") or []
    names = {a.get("name") for a in assets}
    lines.append("release id %s, %d asset(s), draft=%s" % (rel.get("id"), len(assets), bool(rel.get("draft"))))
    absent = [n for n in p["assets"] if n not in names]
    if absent:
        bad.append("missing asset(s): %s" % ", ".join(absent))
    partial = [a.get("name") for a in assets if a.get("state", "uploaded") != "uploaded"]
    if partial:
        bad.append("asset(s) not fully uploaded: %s" % ", ".join(str(n) for n in partial))
    if p["asset_count"] and len(assets) != p["asset_count"]:
        bad.append("%d asset(s), expected exactly %d" % (len(assets), p["asset_count"]))
    if p["notes_file"]:
        try:
            version = p["tag"].lstrip("v")
            notes = p["notes_file"].format(version=version, X_Y_Z=version.replace(".", "_"))
            with open(os.path.join(ctx.repo, notes), "r", encoding="utf-8") as fh:
                want = fh.read()
        except OSError as exc:
            return unknown("cannot read the notes file %s" % p["notes_file"], [str(exc)])
        norm = lambda t: (t or "").replace("\r\n", "\n").strip()
        if norm(rel.get("body")) != norm(want):
            bad.append("the draft body is not identical to %s" % p["notes_file"])
        else:
            lines.append("body equals %s" % p["notes_file"])
    unk = None
    if p["immutable_releases"]:
        imm, ierr = gh_json(ctx, "repos/%s/immutable-releases" % p["repo"])
        if ierr or not isinstance(imm, dict) or not isinstance(imm.get("enabled"), bool):
            unk = "the immutable-releases setting could not be read (%s)" % (ierr or "unexpected response")
        elif not imm["enabled"]:
            bad.append("immutable releases are not enabled for %s" % p["repo"])
        else:
            lines.append("immutable releases: enabled")
    if bad:
        return fail("; ".join(bad), lines)
    if unk:
        return unknown(unk, lines)
    return ok("draft %s (id %s) is in place with %d asset(s)" % (p["tag"], rel.get("id"), len(assets)), lines)
