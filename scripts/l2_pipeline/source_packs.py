"""Non-agent, immutable public-source archives for blind S1 coding.

A successful HTTP request is not a complete-list certification. The coordinator
must supply a dated, scoped declaration with locators verified against archived
text. Source bytes, declarations and scan findings have data-level authority.
"""
from __future__ import annotations

import base64
import codecs
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import unicodedata
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .contracts import DesignCard
from .storage import encoded, local, output_root, public_input, sha, write_once

MAX_BYTES = 30 * 1024 * 1024
HISTORY_TYPES = {"cumulative_ever", "historical_panel", "current_stock"}


def _url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Source must be a public HTTP(S) URL without credentials")
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("Local network sources are not public downloads")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Private network source is not a public download")
    return value


def _public_target(url: str) -> str:
    _url(url)
    host = urlsplit(url).hostname
    for result in socket.getaddrinfo(host, None):
        if not ipaddress.ip_address(result[4][0]).is_global:
            raise ValueError("Source resolves to a private network")
    return url


class _PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_target(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_public(url: str, *, timeout: float = 20, max_bytes: int = MAX_BYTES) -> tuple[bytes, str, str]:
    """One ordinary free/public request; no retries, authentication or bypass."""
    _public_target(url)
    request = Request(url, headers={"User-Agent": "L2-Public-Source-Archive/1.0"})
    with build_opener(_PublicRedirect()).open(request, timeout=timeout) as response:
        resolved = _url(response.geturl())
        size = response.headers.get("Content-Length")
        if size and int(size) > max_bytes:
            raise ValueError("Source exceeds archive size limit")
        raw = response.read(max_bytes + 1)
        if not raw or len(raw) > max_bytes:
            raise ValueError("Empty source or source exceeds archive size limit")
        return raw, resolved, response.headers.get("Content-Type", "application/octet-stream")


class _HTMLText(HTMLParser):
    """Includes comments, hidden text and alt/title metadata for initial checks."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.script_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.script_depth += 1
        for key, value in attrs:
            if key in {"alt", "title", "content"} and value:
                self.parts.append(value)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.script_depth = max(0, self.script_depth - 1)

    def handle_data(self, data):
        if not self.script_depth:
            self.parts.append(data)

    def handle_comment(self, data):
        self.parts.append(data)


def extract_text(raw: bytes, content_type: str) -> tuple[str, str]:
    if raw.startswith(b"%PDF") or "pdf" in content_type.lower():
        try:
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
            return text, "pypdf_text; no OCR or image-text scan"
        except Exception as exc:
            # The reference estimator environment may omit pypdf. Poppler is
            # an offline extraction fallback, never a model/OCR/network call.
            try:
                result=subprocess.run(["pdftotext","-","-"],input=raw,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=False)
                if result.returncode==0:
                    return result.stdout.decode("utf-8",errors="replace"), "Poppler pdftotext fallback; no OCR or image-text scan"
            except (OSError,subprocess.TimeoutExpired):
                pass
            return "", "PDF extraction unavailable/failed: " + type(exc).__name__
    text = raw.decode("utf-8", errors="replace")
    if "html" in content_type.lower() or re.search(r"<html\b|<!doctype\s+html", text[:2000], re.I):
        parser = _HTMLText()
        parser.feed(text)
        return "\n".join(parser.parts), "stdlib HTML text/comments/hidden text/alt metadata; scripts excluded"
    return text, "UTF-8 text extraction"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if c not in "\u200b\u200c\u200d\u2060\ufeff" and not 0xE0000 <= ord(c) < 0xE0080)
    decoded = []
    for token in re.findall(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{20,}={0,2}(?![A-Za-z0-9+/])", text):
        try:
            value = base64.b64decode(token, validate=True).decode("utf-8")
            if value.isprintable():
                decoded.append(value)
        except (ValueError, UnicodeDecodeError):
            continue
    return text + "\n" + "\n".join(decoded) + "\n[ROT13 initial check]\n" + codecs.decode(text, "rot_13")


def scan_text(text: str, *, ml_scanner=None) -> dict:
    """Skill initial check, with honest unavailable-model telemetry.

    ml_scanner is an optional preloaded local detector accepting normalized text;
    this module never installs packages/downloads models or calls a paid API.
    """
    normalized = normalize(text)
    patterns = {
        "override_instructions": r"ignore\s+(?:all\s+)?(?:previous|prior|above|system)\s+instructions",
        "assistant_directive": r"(?:you\s+are\s+(?:chatgpt|an?\s+ai)|(?:AI\s+)?assistant\s+(?:must|should)|system\s+prompt)",
        "secret_exfiltration": r"(?:send|upload|reveal|print)\s+(?:all\s+)?(?:credentials|api\s+keys?|passwords?|secrets?)",
        "role_impersonation": r"<\|(?:system|im_start)\|>|\[INST\]",
    }
    findings = [name for name, pattern in patterns.items() if re.search(pattern, normalized, re.I)]
    ml_result = None
    ml_error = None
    if ml_scanner is not None:
        try:
            ml_result = dict(ml_scanner(normalized))
        except Exception as exc:
            ml_error = type(exc).__name__
    flagged = bool(findings) or bool(ml_result and (ml_result.get("is_valid") is False or ml_result.get("injection") is True))
    return {
        "skill": "detecting-indirect-prompt-injection", "atlas": "AML.T0051.001",
        "method": "local normalization/base64/ROT13 and heuristic initial check",
        "normalized_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        "findings": findings, "suspicious": flagged, "ml_scan_performed": ml_result is not None,
        "ml_result": ml_result, "ml_error": ml_error,
        "llm_guard_package_available": importlib.util.find_spec("llm_guard") is not None,
        "transformers_package_available": importlib.util.find_spec("transformers") is not None,
        "ml_limit": "No configured local detector; no model installation/download or remote scan" if ml_scanner is None else None,
        "clean_scan_proves_safety": False, "source_instructions_have_authority": False,
    }


def _date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("Complete-list dates must be explicit ISO YYYY-MM-DD") from None


def _certification(candidate: dict, card: DesignCard, text: str) -> tuple[bool, list[str]]:
    problems = []
    if candidate.get("complete_list") is not True:
        return False, ["Candidate not certified as a complete list"]
    for field in ("as_of", "coverage_end"):
        try:
            _date(candidate.get(field))
        except ValueError as exc:
            problems.append(field + ": " + str(exc))
    if not problems and _date(candidate["coverage_end"]) > _date(candidate["as_of"]):
        problems.append("Coverage end exceeds the stated as-of date")
    if candidate.get("predicate") != card.payload.get("treatment", {}).get("predicate", card.mechanism):
        problems.append("Complete-list predicate differs from card")
    universe = candidate.get("universe")
    expected = card.payload.get("treatment", {}).get("universe", [])
    if not isinstance(universe, list) or not universe or len(set(universe)) != len(universe):
        problems.append("Complete-list universe must be an explicit distinct nonempty list")
    elif expected and len(set(universe) & set(expected)) < max(1, len(expected) / 2):
        problems.append("List covers fewer than half of the card's units")
    if candidate.get("history_type") not in HISTORY_TYPES:
        problems.append("Historical meaning must be explicit (cumulative_ever/historical_panel/current_stock)")
    verification = candidate.get("verification", {})
    if not isinstance(verification, dict) or verification.get("kind") != "text_anchors":
        problems.append("Missing archive-verified complete-list provenance")
    else:
        anchors = verification.get("anchors", [])
        if not isinstance(anchors, list) or not anchors or any(not isinstance(a, str) or len(a.strip()) < 5 for a in anchors):
            problems.append("Missing substantive archive text anchors")
        elif any(a.casefold() not in text.casefold() for a in anchors):
            problems.append("Complete-list/date/scope anchors absent from archived source text")
        if not verification.get("locator") or not verification.get("scope_basis") or not verification.get("date_basis"):
            problems.append("Missing locator, date basis or scope basis")
    if len(text.strip()) < 200:
        problems.append("Source has insufficient extractable substantive text; browser-only map/empty extraction cannot certify")
    if re.search(r"(?:access denied|verify you are human|checking your browser|enable javascript and cookies)", text[:6000], re.I):
        problems.append("Source appears to be access denial or bot-check page")
    return not problems, problems


def _bytes_once(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("Existing archive bytes changed: " + str(path))
    else:
        with path.open("xb") as stream:
            stream.write(raw)


def build_source_pack(card: DesignCard, *, repo: Path, pack_dir: Path,
                      candidates: list[dict] | None = None, fetcher=None, ml_scanner=None) -> dict:
    """Archive URLs before jobs, and park packs without a usable complete list.

    Optional candidate.path reuses existing public source bytes (never catalog
    stubs), hash-bound by candidate.sha256; local paths are card-directory relative.
    Existing packs resume only with identical card/config bindings and all hashes.
    """
    repo = Path(repo).resolve()
    pack_dir = output_root(Path(pack_dir), repo)
    if candidates is None:
        candidates = [dict(s) for s in card.payload["sources"] if s.get("source_id") != "candidate_manifest"]
    config_sha = hashlib.sha256(encoded(candidates)).hexdigest()
    manifest_path = local(pack_dir, "manifest.json")
    if manifest_path.exists():
        manifest = load_source_pack(manifest_path, card=card)
        if manifest.get("candidate_config_sha256") != config_sha:
            raise ValueError("Existing source-pack configuration changed; use a new attempt directory")
        return manifest
    start = datetime.now(timezone.utc)
    fetcher = fetcher or fetch_public
    sources, failures, downloads = [], [], []
    ids = set()
    for index, candidate in enumerate(candidates):
        source_id = candidate.get("source_id", "source_" + str(index + 1))
        if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", source_id) or source_id in ids:
            raise ValueError("Unsafe or duplicate source identity")
        ids.add(source_id)
        try:
            url = _url(candidate["url"])
            reused_from = None
            if candidate.get("path"):
                source_path = public_input(card.path.parent / candidate["path"])
                if not candidate.get("sha256") or sha(source_path) != candidate["sha256"]:
                    raise ValueError("Reused source must have matching declared SHA256")
                raw = source_path.read_bytes()
                reused_from = str(source_path)
                resolved = url
                content_type = candidate.get("content_type", "application/pdf" if raw.startswith(b"%PDF") else "text/plain")
            else:
                raw, resolved, content_type = fetcher(url)
            if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
                raise ValueError("Source bytes empty or exceed archive size limit")
            _url(resolved)
            # A metadata catalogue cannot satisfy source readiness even when a
            # mistaken config labels it complete_list=True.
            stub = False
            try:
                obj = json.loads(raw)
                stub = isinstance(obj, dict) and ("candidate_complete_lists" in obj or "answer_free_source_catalog" in str(obj.get("schema", "")))
            except (ValueError, UnicodeDecodeError):
                pass
            text, extraction = extract_text(raw, content_type)
            scan = scan_text(text, ml_scanner=ml_scanner)
            complete, problems = _certification(candidate, card, text)
            if stub:
                complete = False
                problems.append("Catalog stub is not archived third-party complete-list source material")
            if scan["suspicious"]:
                complete = False
                problems.append("Initial source check found possible indirect prompt injection; review required")
            extension = ".pdf" if raw.startswith(b"%PDF") else ".html" if "html" in content_type else ".bin"
            archive = local(pack_dir, "archives/" + source_id + extension)
            extracted = local(pack_dir, "archives/" + source_id + ".txt")
            _bytes_once(archive, raw)
            _bytes_once(extracted, text.encode())
            license_text = candidate.get("license") or "Publisher copyright; public access only, no open redistribution license verified"
            record = {k: candidate.get(k) for k in ("as_of", "coverage_end", "predicate", "universe", "history_type", "verification")}
            record.update(source_id=source_id, url=url, resolved_url=resolved,
                          path=str(archive), sha256=sha(archive), size_bytes=len(raw), license=license_text,
                          content_type=content_type, text_path=str(extracted), text_sha256=sha(extracted),
                          text_size_bytes=extracted.stat().st_size, extraction=extraction,
                          complete_list=complete, certification_problems=problems, scan=scan,
                          reused_from=reused_from)
            sources.append(record)
            for path, role in ((archive, "reused_source_archive" if reused_from else "public_source_download"), (extracted, "derived_source_text")):
                downloads.append({"path": str(path), "url": url, "resolved_url": resolved,
                                  "size_bytes": path.stat().st_size, "sha256": sha(path),
                                  "license": license_text, "role": role, "reused_from": reused_from})
        except Exception as exc:
            failures.append({"source_id": source_id, "url": candidate.get("url"),
                             "reason": str(exc), "exception": type(exc).__name__, "retries": 0})
    complete_sources = [s for s in sources if s["complete_list"]]
    covered = set().union(*(set(s["universe"]) for s in complete_sources)) if complete_sources else set()
    expected = set(card.payload.get("treatment", {}).get("universe", []))
    complete = bool(complete_sources)
    manifest = {
        "schema_version": "1.0", "stage": "S1_SOURCE_PACK", "design_id": card.design_id,
        "design_version": card.version, "card_path": str(card.path.resolve()), "card_sha256": sha(card.path),
        "candidate_config_sha256": config_sha, "started_at": start.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": (datetime.now(timezone.utc) - start).total_seconds(),
        "status": "READY" if complete else "PARKED_SOURCE",
        "reason": "Archived dated complete-list source available; coders must verify official starts and historical exposure" if complete else "No fetched source passed dated complete-list scope/provenance certification; do not dispatch coders",
        "reopen_condition": None if complete else "Fetch a substantive dated complete-list source matching predicate/universe with archive-verifiable provenance",
        "complete_list_complete": complete,
        "historical_coverage_complete": any(s["history_type"] in {"cumulative_ever", "historical_panel"} for s in complete_sources),
        "complete_list_source_ids": [s["source_id"] for s in complete_sources],
        "covered_units": sorted(covered & expected), "missing_units": sorted(expected - covered),
        "sources": sources, "fetch_failures": failures, "downloads": downloads,
        "source_instructions_have_authority": False, "outcome_associations_read": False,
    }
    write_once(manifest_path, manifest)
    return manifest


def load_source_pack(manifest_path: Path, card: DesignCard | None = None) -> dict:
    """Validate archive integrity before exposing pack files to a blind coder."""
    path = public_input(Path(manifest_path))
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != "1.0" or manifest.get("stage") != "S1_SOURCE_PACK":
        raise ValueError("Invalid source-pack schema/stage")
    if type(manifest.get("complete_list_complete")) is not bool or type(manifest.get("historical_coverage_complete")) is not bool:
        raise ValueError("Invalid source-pack completeness flags")
    if card is not None and (manifest.get("design_id") != card.design_id or manifest.get("design_version") != card.version or manifest.get("card_sha256") != sha(card.path)):
        raise ValueError("Source pack/card binding differs")
    root = path.parent.resolve()
    for source in manifest.get("sources", []):
        for field, hash_field, size_field in (("path", "sha256", "size_bytes"), ("text_path", "text_sha256", "text_size_bytes")):
            archived = public_input(Path(source[field]))
            if not archived.is_relative_to(root) or not archived.is_file() or sha(archived) != source[hash_field] or archived.stat().st_size != source[size_field]:
                raise ValueError("Source archive path/hash/size changed")
    certified = [s for s in manifest.get("sources", []) if s.get("complete_list") is True]
    if manifest["complete_list_complete"] != bool(certified) or manifest.get("status") != ("READY" if certified else "PARKED_SOURCE"):
        raise ValueError("Source-pack readiness contradicts complete-list evidence")
    if manifest["historical_coverage_complete"] != any(s.get("history_type") in {"cumulative_ever", "historical_panel"} for s in certified):
        raise ValueError("Source-pack historical coverage contradicts evidence")
    if manifest.get("complete_list_source_ids") != [s["source_id"] for s in certified]:
        raise ValueError("Source-pack complete-list identity differs")
    return manifest


# Explicit alias for callers that name their guard by its purpose.
validate_manifest = load_source_pack
