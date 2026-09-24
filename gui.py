import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import subprocess
import sys
import os
import queue
import runpy

# Explicit imports to ensure PyInstaller bundles runtime dependencies
# Requests and related packages are dynamically used by the embedded scripts
import requests
import charset_normalizer
import fake_useragent
import chardet
import idna
import certifi
import urllib3
try:
    # chardet may include mypyc-compiled submodules that PyInstaller misses.
    # Import explicitly to force PyInstaller to include them.
    # import common mypyc-compiled modules under chardet.pipeline
    from chardet.pipeline import (
        ascii__mypyc,
        confusion__mypyc,
        escape__mypyc,
        language__mypyc,
        magic__mypyc,
        orchestrator__mypyc,
        postprocess__mypyc,
        statistical__mypyc,
        structural__mypyc,
        utf1632__mypyc,
        utf8__mypyc,
        validity__mypyc,
    )  # type: ignore
except Exception:
    pass


class BoosterGUI:
    def __init__(self, root):
        self.root = root
        root.title('bilibili-viewcount-booster GUI')

        frm = ttk.Frame(root, padding=10)
        frm.grid(row=0, column=0, sticky='nsew')

        ttk.Label(frm, text='进程数:').grid(row=0, column=0, sticky='w')
        self.concurrency_var = tk.IntVar(value=4)
        ttk.Entry(frm, textvariable=self.concurrency_var, width=10).grid(row=0, column=1, sticky='w')

        ttk.Label(frm, text='目标播放数:').grid(row=1, column=0, sticky='w')
        self.value_var = tk.IntVar(value=248)
        ttk.Entry(frm, textvariable=self.value_var, width=10).grid(row=1, column=1, sticky='w')

        self.start_btn = ttk.Button(frm, text='开始运行', command=self.start)
        self.start_btn.grid(row=2, column=0, pady=8)
        self.stop_btn = ttk.Button(frm, text='结束', command=self.stop, state='disabled')
        self.stop_btn.grid(row=2, column=1, pady=8)

        ttk.Label(frm, text='日志:').grid(row=3, column=0, columnspan=2, sticky='w')
        self.log_box = scrolledtext.ScrolledText(frm, width=80, height=20, state='disabled')
        self.log_box.grid(row=4, column=0, columnspan=2, pady=4)

        root.protocol('WM_DELETE_WINDOW', self.on_close)

        self.proc = None
        self.thread = None
        self.queue = queue.Queue()
        self._stop_event = threading.Event()

    def append_log(self, line: str):
        self.log_box.configure(state='normal')
        self.log_box.insert('end', line)
        self.log_box.see('end')
        self.log_box.configure(state='disabled')

    def read_proc_output(self):
        assert self.proc is not None
        for line in iter(self.proc.stdout.readline, ''):
            if line == '' and self.proc.poll() is not None:
                break
            self.queue.put(line)
        # also read remaining stderr
        for line in iter(self.proc.stderr.readline, ''):
            if line == '' and self.proc.poll() is not None:
                break
            self.queue.put(line)

    def poll_queue(self):
        try:
            while True:
                line = self.queue.get_nowait()
                self.append_log(line)
        except queue.Empty:
            pass
        if self.proc and self.proc.poll() is None:
            self.root.after(100, self.poll_queue)
        else:
            # process ended
            self.append_log('\n[process exited]\n')
            self.start_btn.configure(state='normal')
            self.stop_btn.configure(state='disabled')

    def start(self):
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo('Info', '任务已在运行')
            return
        concurrency = int(self.concurrency_var.get())
        value = int(self.value_var.get())

        # When packaged as single exe, invoke the same exe with --run-fetch so it executes
        # the embedded fetch_and_boost.py instead of starting another GUI instance.
        cmd = [sys.executable, '--run-fetch', '--concurrency', str(concurrency), '--value', str(value)]
        self.append_log(f"启动: {' '.join(cmd)}\n")
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        except Exception as e:
            messagebox.showerror('启动失败', str(e))
            return

        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')

        # start reader thread
        self.thread = threading.Thread(target=self.read_proc_output, daemon=True)
        self.thread.start()
        self.root.after(100, self.poll_queue)

    def stop(self):
        if not self.proc:
            return
        if self.proc.poll() is None:
            try:
                self.append_log('\n请求终止进程...\n')
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except Exception:
                    self.proc.kill()
            except Exception as e:
                self.append_log(f'终止失败: {e}\n')
        self.start_btn.configure(state='normal')
        self.stop_btn.configure(state='disabled')

    def on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno('确认', '任务正在运行，确定要退出并结束任务吗？'):
                return
            self.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = BoosterGUI(root)
    root.mainloop()


if __name__ == '__main__':
    # Support running embedded scripts when this single-file exe is invoked with flags
    # Example: booster_gui.exe --run-fetch --concurrency 4 --value 248
    if '--run-fetch' in sys.argv:
        # locate fetch_and_boost.py inside bundle or source dir
        try:
            if getattr(sys, 'frozen', False):
                base = sys._MEIPASS
            else:
                base = os.path.dirname(__file__)
            idx = sys.argv.index('--run-fetch')
            script_args = sys.argv[idx+1:]
            script_path = os.path.join(base, 'fetch_and_boost.py')
            # set sys.argv for the script
            sys.argv = [script_path] + script_args
            runpy.run_path(script_path, run_name='__main__')
        except Exception as e:
            print(f'run-fetch failed: {e}', file=sys.stderr)
        sys.exit(0)

    if '--run-booster' in sys.argv:
        try:
            if getattr(sys, 'frozen', False):
                base = sys._MEIPASS
            else:
                base = os.path.dirname(__file__)
            idx = sys.argv.index('--run-booster')
            script_args = sys.argv[idx+1:]
            script_path = os.path.join(base, 'booster.py')
            sys.argv = [script_path] + script_args
            runpy.run_path(script_path, run_name='__main__')
        except Exception as e:
            print(f'run-booster failed: {e}', file=sys.stderr)
        sys.exit(0)

    main()
