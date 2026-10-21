from __future__ import annotations

import contextlib
import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kernel_opt_agent.hardware.remote_gpu_detector import QUERY_SCRIPT, _BOOTSTRAP
from kernel_opt_agent.hardware.remote_gpu_query import collect


UUID_ZERO = "GPU-00000000-0000-0000-0000-000000000001"
UUID_OTHER = "GPU-00000000-0000-0000-0000-000000000002"


class RuntimeIdentityTests(unittest.TestCase):
    def query(self, backend, *, uuid=None, pci=None, mask=None, ordered=False):
        properties = SimpleNamespace(name="MetaX C500" if backend == "mxmaca" else "NVIDIA test",
                                     uuid=uuid, pci_bus_id=pci, total_memory=1024)
        cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 1,
                               get_device_properties=lambda index: properties,
                               mem_get_info=lambda index: (512, 1024))
        torch = SimpleNamespace(__version__="test", cuda=cuda,
                                version=SimpleNamespace(maca="3.3" if backend == "mxmaca" else None, cuda="12"))

        def query_tool(arguments, **kwargs):
            if arguments[0] == "mx-smi":
                output = "GPU 0 0000:01:00.0\nGPU 1 0000:02:00.0\n"
            else:
                output = f"0, {UUID_ZERO}, NVIDIA test, 0000:01:00.0, driver-zero\n1, {UUID_OTHER}, NVIDIA test, 0000:02:00.0, driver-other\n"
            return SimpleNamespace(returncode=0, stdout=output)

        prefix = "MACA" if backend == "mxmaca" else "CUDA"
        environment = {prefix + "_VISIBLE_DEVICES": mask} if mask else {}
        if ordered:
            environment[prefix + "_DEVICE_ORDER"] = "PCI_BUS_ID"
        with patch.dict("sys.modules", {"torch": torch}), patch.dict("os.environ", environment, clear=True), \
                patch("kernel_opt_agent.hardware.remote_gpu_query.shutil.which", side_effect=lambda name: name if name in {"mx-smi", "nvidia-smi"} else None), \
                patch("kernel_opt_agent.hardware.remote_gpu_query.os.path.isfile", return_value=False), \
                patch("kernel_opt_agent.hardware.remote_gpu_query.subprocess.run", side_effect=query_tool):
            return collect()

    def test_single_visible_device_does_not_prove_management_index_zero(self):
        for backend in ("mxmaca", "cuda"):
            for mask in (None, "0", "1", UUID_OTHER):
                for ordered in (False, True):
                    with self.subTest(backend=backend, mask=mask, ordered=ordered):
                        device = self.query(backend, pci=16, mask=mask, ordered=ordered)["devices"][0]
                        self.assertFalse(device["identity_verified"])
                        self.assertIsNone(device["fields"]["device_uuid"])
                        self.assertIsNone(device["fields"]["pci_bus_id"])
                        self.assertTrue(any("task binding is disabled" in warning for warning in device["warnings"]))

    def test_runtime_uuid_joins_management_by_uuid_not_ordinal(self):
        payload = self.query("cuda", uuid=UUID_OTHER, mask="0")
        device = payload["devices"][0]
        self.assertTrue(device["identity_verified"])
        self.assertEqual(device["fields"]["device_uuid"], UUID_OTHER)
        self.assertEqual(device["selector"], UUID_OTHER)
        self.assertEqual(payload["environment"]["driver_version"], "driver-other")
        self.assertIsNone(device["fields"]["pci_bus_id"])

    def test_runtime_bdf_is_valid_identity_without_uuid(self):
        for backend in ("mxmaca", "cuda"):
            device = self.query(backend, pci="0000:AB:00.0")["devices"][0]
            self.assertTrue(device["identity_verified"])
            self.assertEqual(device["fields"]["pci_bus_id"], "0000:ab:00.0")

    def test_invalid_runtime_uuid_does_not_establish_identity(self):
        for value in ("unknown", "None", "GPU-invalid"):
            self.assertFalse(self.query("mxmaca", uuid=value)["devices"][0]["identity_verified"])

    def test_metax_runtime_raw_uuid_is_normalized_for_binding(self):
        device = self.query("mxmaca", uuid=UUID_OTHER[4:])["devices"][0]
        self.assertTrue(device["identity_verified"])
        self.assertEqual(device["selector"], UUID_OTHER)
        self.assertEqual(device["device_id"], "mxmaca:" + UUID_OTHER)


class InterpreterDiscoveryTests(unittest.TestCase):
    def payload(self, *, gpu=False, identity=True, interpreter="/system/python"):
        devices = [{"identity_verified": identity, "fields": {"device_uuid": UUID_OTHER}}] if gpu else []
        return {"devices": devices, "environment": {"python_executable": interpreter, "torch_version": "cpu-only" if not gpu else "gpu"}, "warnings": []}

    def completed(self, payload):
        return SimpleNamespace(returncode=0, stdout="KERNEL_AGENT_GPU_QUERY=" + json.dumps(payload))

    def bootstrap(self, execute, *, monotonic=None, realpath=None):
        output = io.StringIO()
        with patch("sys.executable", "/system/python"), patch("glob.glob", return_value=["/opt/conda/envs/gpu/bin/python"]), \
                patch("os.path.isfile", return_value=True), patch("os.path.realpath", side_effect=realpath or (lambda value: value)), \
                patch("subprocess.run", side_effect=execute) as run, contextlib.redirect_stdout(output):
            if monotonic:
                with patch("time.monotonic", side_effect=monotonic):
                    exec(_BOOTSTRAP, {})
            else:
                exec(_BOOTSTRAP, {})
        line = output.getvalue().strip().splitlines()[-1]
        return json.loads(line.split("=", 1)[1]), run

    def test_cpu_only_torch_does_not_hide_gpu_conda_environment(self):
        gpu_python = "/opt/conda/envs/gpu/bin/python"

        def execute(arguments, **kwargs):
            return self.completed(self.payload(gpu=arguments[0] == gpu_python, interpreter=arguments[0]))

        payload, run = self.bootstrap(execute)
        self.assertEqual(payload["environment"]["python_executable"], gpu_python)
        self.assertTrue(payload["devices"][0]["identity_verified"])
        self.assertEqual(run.call_count, 5)
        self.assertTrue(any("no_visible_gpu" in warning for warning in payload["warnings"]))
        for call in run.call_args_list:
            self.assertEqual(call.args[0][1:], ["-B", "-c", QUERY_SCRIPT])
            self.assertGreater(call.kwargs["timeout"], 0)
            self.assertLessEqual(call.kwargs["timeout"], 15)

    def test_timeout_and_invalid_output_continue_without_leaking_exception(self):
        count = [0]

        def execute(arguments, **kwargs):
            count[0] += 1
            if count[0] == 1:
                raise subprocess.TimeoutExpired("credential-sentinel", kwargs["timeout"])
            if count[0] == 2:
                return SimpleNamespace(returncode=0, stdout="credential-sentinel")
            return self.completed(self.payload(gpu=True, interpreter=arguments[0]))

        payload, run = self.bootstrap(execute)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(payload["environment"]["python_executable"], "/opt/conda/bin/python")
        self.assertIn("interpreter[0]: timeout", payload["warnings"])
        self.assertNotIn("credential-sentinel", json.dumps(payload))

    def test_environment_symlinks_to_same_binary_are_still_queried(self):
        def execute(arguments, **kwargs):
            return self.completed(self.payload(gpu=arguments[0].startswith("/opt/conda/envs/"), interpreter=arguments[0]))

        payload, run = self.bootstrap(execute, realpath=lambda value: "/shared/python")
        self.assertEqual(run.call_count, 5)
        self.assertEqual(payload["environment"]["python_executable"], "/opt/conda/envs/gpu/bin/python")

    def test_unverified_devices_do_not_prevent_trying_another_environment(self):
        def execute(arguments, **kwargs):
            return self.completed(self.payload(gpu=True, identity=arguments[0] != "/system/python", interpreter=arguments[0]))

        payload, run = self.bootstrap(execute)
        self.assertEqual(run.call_count, 2)
        self.assertTrue(payload["devices"][0]["identity_verified"])
        self.assertIn("interpreter[0]: stable_identity_unavailable", payload["warnings"])

    def test_all_cpu_only_environments_return_partial_query_not_a_crash(self):
        payload, run = self.bootstrap(lambda arguments, **kwargs: self.completed(self.payload()))
        self.assertEqual(run.call_count, 5)
        self.assertEqual(payload["devices"], [])
        self.assertEqual(len(payload["warnings"]), 5)

    def test_overall_deadline_bounds_discovery(self):
        payload, run = self.bootstrap(lambda arguments, **kwargs: self.completed(self.payload()), monotonic=[0, 1, 51])
        self.assertEqual(run.call_count, 1)
        self.assertIn("interpreter discovery: total_timeout", payload["warnings"])


if __name__ == "__main__":
    unittest.main()
