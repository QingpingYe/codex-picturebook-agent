# -*- coding: utf-8 -*-
"""run_capture.py 离线单测（无网络）。

覆盖：正常退出与输出落盘 / 非零退出码透传 / 乱码字节原样保存 /
_capture 目录自动创建 / --echo 全量打印。
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _run_capture(args, timeout=60):
    return subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "run_capture.py")] + args,
        capture_output=True, timeout=timeout)


class TestRunCapture(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.mkdtemp()

    def _captured(self, name):
        return os.path.join(self.workdir, "feishu_sync_tmp", "_capture",
                            name + ".txt")

    def test_captures_stdout_and_exit(self):
        p = _run_capture(["--cmd", sys.executable + " -c \"print('hello capture')\"",
                          "--out", "t1", "--workdir", self.workdir])
        self.assertEqual(p.returncode, 0)
        out = p.stdout.decode("utf-8", errors="replace")
        self.assertIn("EXIT=0", out)
        with open(self._captured("t1"), "rb") as f:
            raw = f.read().decode("utf-8", errors="replace")
        self.assertIn("hello capture", raw)

    def test_nonzero_exit_propagated_and_reported(self):
        p = _run_capture(["--cmd",
                          sys.executable + " -c \"import sys; print('boom'); sys.exit(3)\"",
                          "--out", "t2", "--workdir", self.workdir])
        self.assertEqual(p.returncode, 3)
        self.assertIn("EXIT=3", p.stdout.decode("utf-8", errors="replace"))

    def test_garbled_bytes_preserved_on_disk(self):
        cmd = (sys.executable +
               " -c \"import sys; sys.stdout.buffer.write('中文GBK'.encode('gbk'))\"")
        p = _run_capture(["--cmd", cmd, "--out", "t3", "--workdir", self.workdir])
        self.assertEqual(p.returncode, 0)
        with open(self._captured("t3"), "rb") as f:
            raw = f.read()
        self.assertEqual(raw, "中文GBK".encode("gbk"))

    def test_capture_dir_autocreated(self):
        _run_capture(["--cmd", sys.executable + " -c \"print('x')\"",
                      "--out", "t4", "--workdir", self.workdir])
        self.assertTrue(os.path.isdir(
            os.path.join(self.workdir, "feishu_sync_tmp", "_capture")))

    def test_echo_prints_full_output(self):
        cmd = sys.executable + " -c \"print('line-a'); print('line-b')\""
        p = _run_capture(["--cmd", cmd, "--out", "t5", "--workdir", self.workdir,
                          "--echo"])
        out = p.stdout.decode("utf-8", errors="replace")
        self.assertIn("line-a", out)
        self.assertIn("line-b", out)


if __name__ == "__main__":
    unittest.main()
