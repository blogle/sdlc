import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("fleet", Path(__file__).parents[1] / "src/fleet.py")
fleet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fleet)


HEAD = "a" * 40


def manifest():
    return {
        "schema": 1,
        "owner": {"login": "blogle", "id": 1},
        "repositories": [{"name": "anvil", "id": 2, "enabled": True}],
    }


def policy():
    return {
        "schema": 1,
        "provenance": {"source": "blogle/sdlc", "revision": HEAD},
        "security_floor": {
            "required_checks": ["sdlc-check", "sdlc-policy"],
            "protected_default_branch": True,
            "protected_release_tags": True,
            "no_force_push": True,
            "no_destructive_changes": True,
            "require_pull_requests": True,
            "squash_only": True,
            "dismiss_stale_reviews": True,
            "integration_path": "merge_queue",
            "trusted_publishers": {
                "sdlc-check": "blogle/sdlc",
                "sdlc-policy": "blogle/sdlc",
                "sdlc-verify": "blogle/sdlc",
            },
        },
        "bypass": {"kind": "personal_user", "user_id": 7, "actors": [7], "count": 1},
    }


class FleetContractTests(unittest.TestCase):
    def test_policy_requires_trusted_single_user(self):
        valid = policy()
        valid["security_floor"]["required_checks"] = ["sdlc-check", "sdlc-policy"]
        fleet.validate_policy(valid, trusted_bypass_user_id=7)
        with self.assertRaisesRegex(ValueError, "trusted fleet"):
            fleet.validate_policy(policy(), trusted_bypass_user_id=8)

    def test_manifest_rejects_missing_immutable_id(self):
        invalid = manifest()
        invalid["repositories"][0].pop("id")
        with self.assertRaisesRegex(ValueError, "immutable repository"):
            fleet.validate_manifest(invalid)

    def test_alias_migration_requires_new_checks_before_requirement(self):
        result = fleet.migration_plan([], [], phase="bootstrap")
        self.assertEqual(result["operations"], ["publish stable wrappers"])
        result = fleet.migration_plan(["sdlc / pr-fast"], fleet.ALL_CHECK_NAMES, phase="dual", exact_head=True)
        self.assertEqual(result["required_checks"], ["sdlc-check", "sdlc-policy"])
        self.assertTrue(result["retain_aliases"])
        with self.assertRaisesRegex(ValueError, "before they exist"):
            fleet.migration_plan([], ["sdlc-check"], phase="dual", exact_head=True)

    def test_plan_is_prepare_only_and_checks_head(self):
        result = fleet.plan(
            manifest(), policy(), trusted_bypass_user_id=7,
            verifier=type("Verifier", (), {"verify": lambda self, source, revision: {"source": source, "repository_id": 1, "commit_sha": revision, "reachable_from_protected": True, "immutable": True}})(),
            consumer_pin=HEAD, expected_head=HEAD, phase="bootstrap",
            current_checks=["sdlc / pr-fast"], available_checks=fleet.ALL_CHECK_NAMES,
        )
        self.assertTrue(result["prepare_only"])
        self.assertEqual(result["mutations"], [])
        with self.assertRaisesRegex(ValueError, "does not match"):
            fleet.plan(manifest(), policy(), trusted_bypass_user_id=7, verifier=type("Verifier", (), {"verify": lambda self, source, revision: {"source": source, "repository_id": 1, "commit_sha": revision, "reachable_from_protected": True, "immutable": True}})(), consumer_pin=HEAD,
                       expected_head="b" * 40, available_checks=fleet.ALL_CHECK_NAMES)

    def test_release_identity_and_policy_version_fail_closed(self):
        identity = fleet.verify_release_identity(
            expected_head=HEAD, actual_head=HEAD, expected_version="1.2.3",
            actual_version="1.2.3", source_main_sha=HEAD,
        )
        self.assertEqual(identity["version"], "1.2.3")
        with self.assertRaisesRegex(ValueError, "unsupported policy version"):
            fleet.check_policy_compatibility(2)
        with self.assertRaisesRegex(ValueError, "version changed"):
            fleet.verify_release_identity(
                expected_head=HEAD, actual_head=HEAD, expected_version="1.2.3",
                actual_version="1.2.4", source_main_sha=HEAD,
            )

    def test_generated_callers_are_central_and_pinned(self):
        callers = fleet.render_caller_workflows("v2.0.0")[".github/workflows/sdlc.yml"]
        self.assertIn("name: sdlc-check", callers)
        self.assertIn("name: sdlc / candidate", callers)
        self.assertIn("if: always()", callers)
        self.assertIn("stage: verify", callers)

    def test_provenance_rejects_forged_fork_and_toctou(self):
        verifier = type("Verifier", (), {"verify": lambda self, source, revision: {"source": "evil/fork", "repository_id": 1, "commit_sha": revision, "reachable_from_protected": True, "immutable": True}})()
        with self.assertRaisesRegex(ValueError, "incomplete source identity"):
            fleet.verify_provenance(policy()["provenance"], verifier=verifier, consumer_pin=HEAD)
        verifier = type("Verifier", (), {"verify": lambda self, source, revision: {"source": source, "repository_id": 1, "commit_sha": "b" * 40, "reachable_from_protected": True, "immutable": True}})()
        with self.assertRaisesRegex(ValueError, "differs"):
            fleet.verify_provenance(policy()["provenance"], verifier=verifier, consumer_pin=HEAD)

    def test_floor_rejects_queue_check_as_normal_required_context(self):
        invalid = policy()
        invalid["security_floor"]["required_checks"].append("sdlc-verify")
        invalid["security_floor"]["required_checks"].sort()
        with self.assertRaisesRegex(ValueError, "never require sdlc-verify"):
            fleet.validate_policy(invalid, trusted_bypass_user_id=7)

    def test_renderer_and_reconciler_are_injected_and_prepare_only(self):
        rendered = fleet.render_canonical_policy("main", policy())
        self.assertEqual(rendered["branch"]["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])

        class Client:
            def read(self, observed):
                return {"rulesets": [], "workflow_digest": "old"}

        result = fleet.reconcile_repository(
            {"rulesets": rendered, "workflow_digest": "new"},
            {"state": "ready"}, client=Client(),
        )
        self.assertEqual(result["state"], "unsafe")
        self.assertEqual(result["mutations"], [])
        with self.assertRaisesRegex(ValueError, "disabled"):
            fleet.reconcile_repository({}, {}, client=Client(), apply=True)

    def test_identity_credentials_and_state_machine_fail_closed(self):
        self.assertEqual(fleet.verify_identity(1, 2, {"owner_id": 1, "repository_id": 2, "default_branch": "main"})["default_branch"], "main")
        with self.assertRaisesRegex(ValueError, "replaced"):
            fleet.verify_identity(1, 2, {"owner_id": 1, "repository_id": 3, "default_branch": "main"})
        ready = fleet.credential_readiness({"permissions": {"administration": "write", "actions_secrets": "write", "actions_variables": "write", "contents": "read"}, "installation_verified": True, "generation": 4})
        self.assertEqual(ready["generation"], 4)
        self.assertEqual(fleet.next_state("active", "transient"), "active")
        self.assertEqual(fleet.next_state("active", "unsafe"), "unsafe")


if __name__ == "__main__":
    unittest.main()
