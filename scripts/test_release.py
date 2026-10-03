import copy
import subprocess
import unittest
from unittest.mock import Mock, patch
import json

from check_release import validate_run
import watch_deploy
import release


class ReleaseSafetyTests(unittest.TestCase):
    def test_promotion_requires_a_successful_main_staging_deployment(self):
        run = {"path": ".github/workflows/deploy.yml", "head_branch": "main",
               "event": "push", "conclusion": "success", "head_sha": "a" * 40}
        jobs = [{"name": "Deploy staging", "conclusion": "success"}]
        self.assertEqual(validate_run(run, jobs), "a" * 40)
        for key, value in [("head_branch", "feature"), ("event", "pull_request"),
                           ("conclusion", "failure"), ("path", "other.yml"), ("head_sha", "bad")]:
            invalid = copy.deepcopy(run)
            invalid[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_run(invalid, jobs)
        for invalid in [[], [{"name": "Deploy production", "conclusion": "success"}],
                        [{"name": "Deploy staging", "conclusion": "failure"}]]:
            with self.assertRaises(ValueError):
                validate_run(run, invalid)

    @patch("watch_deploy.aws", return_value=[])
    @patch("watch_deploy.progress", side_effect=RuntimeError("unavailable"))
    @patch("watch_deploy.subprocess.Popen")
    def test_progress_failure_cannot_hide_operation_failure(self, popen, progress, aws):
        process = Mock()
        process.poll.return_value = 42
        popen.return_value = process
        with patch("sys.argv", ["watch", "--stack", "clinicforce-staging", "--", "false"]):
            self.assertEqual(watch_deploy.main(), 42)

    @patch("watch_deploy.progress")
    @patch("watch_deploy.subprocess.Popen")
    def test_monitor_timeout_fails_and_stops_local_waiter(self, popen, progress):
        process = Mock()
        process.poll.return_value = None
        popen.return_value = process
        with patch("sys.argv", ["watch", "--stack", "clinicforce-staging", "--timeout", "-1", "--", "sleep", "60"]):
            self.assertEqual(watch_deploy.main(), 124)
        process.terminate.assert_called_once()

    def test_aws_query_failure_does_not_print_raw_response(self):
        with patch("watch_deploy.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "sensitive")):
            with self.assertRaisesRegex(RuntimeError, "^AWS progress query failed;"):
                watch_deploy.aws("ecs", "describe-services")

    @patch("release.command")
    @patch("release.aws")
    @patch("release.subprocess.run")
    def test_rollback_cannot_publish_frontend(self, run, aws, command):
        manifest = {"sha": "a" * 40, **{s: f"311057178360.dkr.ecr.us-east-1.amazonaws.com/clinicforce-staging-{s}@sha256:" + "b" * 64 for s in ["backend", "hapi"]}}
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        aws.side_effect = [
            {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]},
            ["EnvironmentName", "BackendImageUri", "HapiImageUri", "EcwBackendIssuer"],
            {"services": [{"taskDefinition": "old-task"}]},
            {"taskDefinition": {"containerDefinitions": [{"name": "backend", "image": "old-image"}]}},
        ]
        with patch("sys.argv", ["release", "staging", "--sha", "a" * 40]), \
                patch("release.Path.read_text", return_value=json.dumps(manifest)), \
                patch("release.Path.is_file", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "rolled back"):
                release.main()
        self.assertFalse(any("s3" in call.args for call in command.call_args_list))
        parameters = json.loads(run.call_args.args[0][-1])
        self.assertIn({"ParameterKey": "EcwBackendIssuer", "UsePreviousValue": True}, parameters)


if __name__ == "__main__":
    unittest.main()
