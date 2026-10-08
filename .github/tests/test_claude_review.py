"""Execute the trusted workflow's shell steps against isolated Git/API fixtures."""

import hashlib
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = "atem_ai_vision_mixer/perception/models/silero_vad.onnx"
PIN = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
FIXTURE_MODEL = b"\x00pinned-model-byte-sentinel\xff"


def step_script(filename, name):
    lines = (ROOT / ".github/workflows" / filename).read_text().splitlines()
    start = lines.index(f"      - name: {name}")
    start = lines.index("        run: |", start) + 1
    end = start
    while end < len(lines) and (not lines[end] or lines[end].startswith("          ")):
        end += 1
    return textwrap.dedent("\n".join(lines[start:end])) + "\n"


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "pr-head"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.root / "AGENTS.md").write_text("Trusted instructions\n")
        prompts = self.root / ".github/review-prompts"
        prompts.mkdir(parents=True)
        (prompts / "claude.md").write_text("Review text and metadata only\n")

    def git(self, *args, data=None):
        env = dict(
            os.environ,
            GIT_AUTHOR_NAME="Fixture",
            GIT_AUTHOR_EMAIL="fixture@example.invalid",
            GIT_COMMITTER_NAME="Fixture",
            GIT_COMMITTER_EMAIL="fixture@example.invalid",
        )
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            input=data,
            capture_output=True,
            check=True,
            env=env,
        ).stdout.strip()

    def revision(self, entries, parent=None):
        self.git("read-tree", "--empty")
        for path, value in entries.items():
            mode, data = value if isinstance(value, tuple) else ("100644", value)
            blob = self.git("hash-object", "-w", "--stdin", data=data).decode()
            self.git("update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}")
        tree = self.git("write-tree").decode()
        args = ["commit-tree", tree, "-m", "fixture"]
        if parent:
            args.extend(["-p", parent])
        return self.git(*args).decode()

    def prepare(self, before, after):
        base = self.revision(before)
        head = self.revision(after, base)
        self.git("update-ref", "HEAD", head)
        self.git("checkout-index", "--all", "--force")
        script = step_script("claude-review.yml", "Prepare read-only review context")
        # Unit fixtures exercise the exact filter with tiny synthetic pinned bytes.
        # The production pin and the actual PR29 asset receive a separate real proof.
        script = script.replace(PIN, hashlib.sha256(FIXTURE_MODEL).hexdigest())
        script = script.replace("2327524", str(len(FIXTURE_MODEL)))
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=self.root,
            check=False,
            env=dict(
                os.environ, BASE_SHA=base, HEAD_SHA=head, RUNNER_TEMP=str(self.root)
            ),
            capture_output=True,
        )
        return result

    def test_pinned_asset_is_metadata_only_even_when_forced_text(self):
        result = self.prepare(
            {"app.py": b"old = 1\n"},
            {
                "app.py": b"new = 2\n",
                MODEL: FIXTURE_MODEL,
                "atem_ai_vision_mixer/perception/models/LICENSE": b"MIT License\n",
                ".gitattributes": b"*.onnx diff\n",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        context = (self.root / "review-context/input.txt").read_bytes()
        self.assertNotIn(b"pinned-model-byte-sentinel", context)
        self.assertIn(MODEL.encode(), context)
        self.assertIn(hashlib.sha256(FIXTURE_MODEL).hexdigest().encode(), context)
        self.assertIn(b"new = 2", context)
        sources = (self.root / "review-context/sources.jsonl").read_text().splitlines()
        self.assertIn("app.py", {json.loads(line)["path"] for line in sources})

    def test_production_pin_is_exact_and_not_read_from_pr(self):
        script = step_script("claude-review.yml", "Prepare read-only review context")
        self.assertIn(PIN, script)
        self.assertIn("2327524", script)
        self.assertIn(MODEL, script)

    def test_ordinary_text_review_still_works(self):
        result = self.prepare({"app.py": b"x = 1\n"}, {"app.py": b"x = 2\n"})
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_other_binary_stays_blocked(self):
        result = self.prepare({}, {"other.onnx": FIXTURE_MODEL})
        self.assertNotEqual(result.returncode, 0)

    def test_same_size_tampering_at_pinned_path_stays_blocked(self):
        result = self.prepare({}, {MODEL: b"x" + FIXTURE_MODEL[1:]})
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_size_at_pinned_path_stays_blocked(self):
        result = self.prepare({}, {MODEL: FIXTURE_MODEL + b"x"})
        self.assertNotEqual(result.returncode, 0)

    def test_untrusted_base_asset_is_not_hidden_by_deletion(self):
        result = self.prepare({MODEL: b"bad\x00bytes"}, {})
        self.assertNotEqual(result.returncode, 0)

    def test_symlink_at_pinned_path_is_rejected(self):
        result = self.prepare({}, {MODEL: ("120000", b"target")})
        self.assertNotEqual(result.returncode, 0)

    def test_submodule_at_pinned_path_is_rejected(self):
        result = self.prepare({}, {MODEL: ("160000", b"external commit")})
        self.assertNotEqual(result.returncode, 0)

    def test_credential_content_in_unchanged_source_is_rejected(self):
        secret = b"token = '" + b"ghp_" + b"a" * 30 + b"'\n"
        result = self.prepare(
            {"existing.py": secret, "app.py": b"x = 1\n"},
            {"existing.py": secret, "app.py": b"x = 2\n"},
        )
        self.assertNotEqual(result.returncode, 0)

    def test_deleting_verified_asset_includes_metadata(self):
        result = self.prepare({MODEL: FIXTURE_MODEL}, {})
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        context = (self.root / "review-context/input.txt").read_bytes()
        self.assertNotIn(b"pinned-model-byte-sentinel", context)
        self.assertIn(MODEL.encode(), context)

    def test_renaming_asset_to_unapproved_path_is_rejected(self):
        result = self.prepare({MODEL: FIXTURE_MODEL}, {"renamed.onnx": FIXTURE_MODEL})
        self.assertNotEqual(result.returncode, 0)

    def test_sensitive_text_and_deleted_forced_text_binary_are_rejected(self):
        cases = [
            ({}, {"transcript.md": b"private text"}),
            (
                {"removed.py": b"binary\x00payload", ".gitattributes": b"*.py diff\n"},
                {".gitattributes": b"*.py diff\n"},
            ),
        ]
        for before, after in cases:
            with self.subTest(paths=list(before) + list(after)):
                result = self.prepare(before, after)
                self.assertNotEqual(result.returncode, 0)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sha = "a" * 40
        self.repo = "owner/repository"
        self.run = {
            "path": ".github/workflows/ci.yml",
            "event": "pull_request",
            "conclusion": "success",
            "head_sha": self.sha,
            "head_repository": {"full_name": self.repo},
            "pull_requests": [{"number": 29}],
        }
        self.pr = {
            "state": "open",
            "draft": True,
            "base": {"ref": "main", "sha": "b" * 40},
            "head": {"sha": self.sha, "repo": {"full_name": self.repo}},
        }
        self.checks = [
            {"name": name, "bucket": "pass"}
            for name in [
                "lint",
                "test",
                "build",
                "integration",
                "dependency-review",
                "CodeQL",
            ]
        ]
        mock = self.root / "gh"
        mock.write_text(
            "#!/usr/bin/env python3\n"
            + textwrap.dedent("""\
            import json, os, sys
            from pathlib import Path
            data = json.loads(Path(os.environ['MOCK_DATA']).read_text())
            args = sys.argv[1:]
            if args[0] == 'api':
                request = next(a for a in args if a.startswith('repos/'))
                print(json.dumps([data.get('reviews', [])] if request.endswith('/reviews') else data['run' if '/actions/runs/' in request else 'pr']))
            elif args[:2] == ['pr', 'checks']:
                print(json.dumps(data['checks']))
            elif args[:2] == ['workflow', 'run']:
                Path(os.environ['MOCK_DISPATCH']).write_text(json.dumps(args))
            else:
                raise SystemExit('Unexpected gh call')
            """)
        )
        mock.chmod(0o755)
        sleep = self.root / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n")
        sleep.chmod(0o755)

    def dispatch(self):
        data = self.root / "data.json"
        data.write_text(
            json.dumps({"run": self.run, "pr": self.pr, "checks": self.checks})
        )
        result = subprocess.run(
            [
                "bash",
                "-c",
                step_script(
                    "claude-review-auto.yml", "Dispatch review after successful checks"
                ),
            ],
            cwd=self.root,
            capture_output=True,
            check=False,
            env=dict(
                os.environ,
                PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                MOCK_DATA=str(data),
                MOCK_DISPATCH=str(self.root / "dispatch.json"),
                CI_RUN_ID="123",
                REPOSITORY=self.repo,
                DEFAULT_BRANCH="main",
            ),
        )
        return result, self.root / "dispatch.json"

    def test_current_draft_with_all_checks_passed_is_dispatched_from_main(self):
        result, output = self.dispatch()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        args = json.loads(output.read_text())
        self.assertIn("claude-review.yml", args)
        self.assertIn("head_sha=" + self.sha, args)
        self.assertIn("pr_number=29", args)
        self.assertEqual(args[args.index("--ref") + 1], "main")

    def test_ineligible_runs_and_prs_never_dispatch(self):
        scenarios = [
            lambda: self.run.update(conclusion="failure"),
            lambda: self.run.update(event="push"),
            lambda: self.run.update(path=".github/workflows/other.yml"),
            lambda: self.run.update(head_repository={"full_name": "fork/repository"}),
            lambda: self.pr.update(draft=False),
            lambda: self.pr.update(state="closed"),
            lambda: self.pr["head"].update(sha="b" * 40),
            lambda: self.pr["head"].update(repo={"full_name": "fork/repository"}),
            lambda: self.pr["base"].update(ref="other"),
        ]
        original_run, original_pr = json.dumps(self.run), json.dumps(self.pr)
        for change in scenarios:
            with self.subTest(change=change):
                self.run, self.pr = json.loads(original_run), json.loads(original_pr)
                change()
                _, output = self.dispatch()
                self.assertFalse(output.exists())

    def test_missing_or_failed_checks_do_not_dispatch(self):
        self.checks.pop()
        _, output = self.dispatch()
        self.assertFalse(output.exists())
        self.checks.append({"name": "CodeQL", "bucket": "fail"})
        _, output = self.dispatch()
        self.assertFalse(output.exists())

    def preflight(self, reviews):
        data = self.root / "data.json"
        data.write_text(json.dumps({"pr": self.pr, "reviews": reviews}))
        output = self.root / "output.txt"
        result = subprocess.run(
            [
                "bash",
                "-c",
                step_script(
                    "claude-review.yml", "Validate the frozen draft pull request"
                ),
            ],
            cwd=self.root,
            capture_output=True,
            check=False,
            env=dict(
                os.environ,
                PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                MOCK_DATA=str(data),
                GITHUB_OUTPUT=str(output),
                SUMMARY_MARKER="<!-- manual-claude-review:v1 -->",
                DEFAULT_BRANCH="main",
                WORKFLOW_REF="refs/heads/main",
                EXPECTED_HEAD_SHA=self.sha,
                PR_NUMBER="29",
                REPOSITORY=self.repo,
            ),
        )
        return result, output.read_text() if output.exists() else ""

    def test_duplicate_marked_scope_skips_another_model_run(self):
        review = {
            "commit_id": self.sha,
            "user": {"login": "github-actions[bot]"},
            "body": "<!-- manual-claude-review:v1 -->\nBase SHA: "
            + "b" * 40
            + "\nReviewed SHA: "
            + self.sha,
        }
        result, output = self.preflight([review])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn("should_review=false", output)
        self.assertNotIn("should_review=true", output)

    def test_new_scope_runs_but_stale_scope_stops_before_status(self):
        result, output = self.preflight([])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn("should_review=true", output)
        (self.root / "output.txt").unlink()
        self.pr["head"]["sha"] = "c" * 40
        result, output = self.preflight([])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, "")

    def test_dispatcher_has_no_checkout_or_model_secret(self):
        workflow = (ROOT / ".github/workflows/claude-review-auto.yml").read_text()
        self.assertNotIn("actions/checkout", workflow)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", workflow)
        self.assertNotIn("download-artifact", workflow)


if __name__ == "__main__":
    unittest.main()
