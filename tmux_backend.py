import os
import re
import shutil
import subprocess


class TmuxError(RuntimeError):
    pass


class TmuxBackend:
    def __init__(self, socket_path, source="web", managed=True):
        self.socket_path = socket_path
        self.source = source
        self.managed = managed
        self.executable = shutil.which("tmux")

    @property
    def available(self):
        return bool(self.executable and (self.socket_path is None or os.path.exists(self.socket_path)))

    def name(self, session_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id):
            raise ValueError("invalid session id")
        return "wt_" + session_id

    def command(self, *args):
        if not self.executable:
            raise TmuxError("tmux is not installed")
        socket_args = ["-S", self.socket_path] if self.socket_path is not None else []
        return [self.executable, "-N", *socket_args, *args]

    def run(self, *args):
        env = {k: v for k, v in os.environ.items() if k not in ("TOKEN", "TMUX", "TMUX_PANE")}
        try:
            return subprocess.run(
                self.command(*args), env=env, capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TmuxError("tmux backend is unavailable") from exc

    def list_sessions(self):
        if not self.available:
            return []
        result = self.run(
            "list-sessions", "-F",
            "#{session_id}\t#{session_created}\t#{pid}\t#{@web_terminal_id}\t#{session_name}",
        )
        if result.returncode:
            if any(message in result.stderr for message in ("no sessions", "no server running", "error connecting")):
                return []
            raise TmuxError("cannot list tmux sessions; check web-terminal-tmux.service")
        items = []
        for line in result.stdout.splitlines():
            fields = line.split("\t", 4)
            if len(fields) != 5:
                continue
            target, created, pid, tagged_id, name = fields
            if not re.fullmatch(r"\$\d+", target) or not pid.isdecimal() or not created.isdecimal():
                continue
            owned_id = tagged_id or (name[3:] if name.startswith("wt_") else "")
            owned = self.managed and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", owned_id))
            session_id = owned_id if owned else f"tmux_external_{self.source}_{pid}_{target[1:]}"
            items.append({
                "id": session_id, "backend": "tmux", "name": name,
                "source": self.source, "managed": owned, "tmux_id": target, "server_pid": pid,
                "created_at": float(created), "clients": 0,
            })
        return items

    def status(self):
        if not self.available:
            return False
        try:
            return self.run("show-options", "-s", "-v", "exit-empty").returncode == 0
        except TmuxError:
            return False

    def create(self, session_id, shell, cwd, rows, cols, colorterm):
        if not self.managed:
            raise TmuxError("external tmux servers are attach-only")
        if not self.available:
            raise TmuxError("tmux backend is unavailable; start web-terminal-tmux.service")
        result = self.run(
            "new-session", "-d", "-P", "-F", "#{session_id}", "-s", self.name(session_id), "-c", cwd,
            "-x", str(cols), "-y", str(rows), "-e", "COLORTERM=" + colorterm,
            shell,
        )
        if result.returncode:
            raise TmuxError("cannot create tmux session")
        target = result.stdout.strip()
        self.run("set-option", "-t", self.target(session_id, target), "@web_terminal_id", session_id)

    def target(self, session_id, target=None):
        self.name(session_id)
        if target is not None:
            if not re.fullmatch(r"\$\d+", target):
                raise ValueError("invalid tmux session target")
            return target
        return "=" + self.name(session_id)

    def attach_command(self, session_id, target=None):
        return self.command("attach-session", "-E", "-t", self.target(session_id, target))

    def kill(self, session_id, target=None):
        if not self.managed:
            raise TmuxError("external tmux sessions may only be detached")
        result = self.run("kill-session", "-t", self.target(session_id, target))
        if result.returncode:
            raise TmuxError("cannot close tmux session")

    def rename(self, session_id, target, name, managed=False):
        if any(character in name for character in ".:"):
            raise ValueError("tmux names cannot contain '.' or ':'")
        target = self.target(session_id, target)
        if managed:
            tagged = self.run("set-option", "-t", target, "@web_terminal_id", session_id)
            if tagged.returncode:
                raise TmuxError("cannot preserve tmux session identity")
        result = self.run("rename-session", "-t", target, name)
        if result.returncode:
            raise TmuxError("cannot rename tmux session; the name may already be in use")

    def refresh(self, session_id, client_pid, target=None):
        result = self.run(
            "list-clients", "-t", self.target(session_id, target),
            "-F", "#{client_pid}\t#{client_tty}",
        )
        if result.returncode:
            return
        for line in result.stdout.splitlines():
            pid, _, tty = line.partition("\t")
            if pid == str(client_pid):
                self.run("refresh-client", "-t", tty)
