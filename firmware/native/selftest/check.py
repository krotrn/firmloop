"""Native self-test driver (task 1.7/1.8). Run by run.sh inside the runner image.

Usage: check.py <build_dir>   (build_dir already holds fake_hal.o and harness_main.o)

For every case: compile logic/<name>.cpp + channels.generated.cpp with the ARCHITECTURE 8.2 flags,
link statically, run with the 8.3 rlimits and classify the result, then check assertions.
Prints PASS/FAIL per case; exit code 1 if any case fails.
"""
import json
import os
import resource
import signal
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "..", "lib", "FirmLoop", "src")
CXXFLAGS = ["-std=c++17", "-O1", "-pipe", "-Wall", "-Wextra", "-Werror=return-type",
            "-Werror=uninitialized", "-fno-exceptions", "-fno-rtti", "-fstack-protector-strong",
            "-I" + LIB]


def limits():
    # soft 2 s -> SIGXCPU (classified timeout); hard 3 s as backstop. soft == hard would deliver
    # SIGKILL straight away and be misreported as "killed".
    resource.setrlimit(resource.RLIMIT_CPU, (2, 3))
    resource.setrlimit(resource.RLIMIT_AS, (128 << 20, 128 << 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))
    resource.setrlimit(resource.RLIMIT_NOFILE, (8, 8))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


def build(build_dir, name, logic=None, harness_defs=()):
    src = os.path.join(HERE, "logic", (logic or name) + ".cpp")
    if not os.path.exists(src):
        return None, "missing logic file " + src
    objs = []
    for s, o in ((src, name + ".o"), (os.path.join(HERE, "channels.generated.cpp"), "channels.o")):
        p = subprocess.run(["g++", *CXXFLAGS, "-c", s, "-o", os.path.join(build_dir, o)],
                           capture_output=True, text=True)
        if p.returncode:
            return None, p.stderr
        objs.append(os.path.join(build_dir, o))
    harness = os.path.join(build_dir, "harness_main.o")
    if harness_defs:  # variant harness (e.g. tiny snapshot buffer)
        harness = os.path.join(build_dir, name + "_h.o")
        p = subprocess.run(["g++", *CXXFLAGS, "-I" + os.path.join(HERE, ".."), *harness_defs, "-c",
                            os.path.join(HERE, "..", "harness_main.cpp"), "-o", harness],
                           capture_output=True, text=True)
        if p.returncode:
            return None, p.stderr
    exe = os.path.join(build_dir, name + ".sim")
    p = subprocess.run(["g++", "-static", "-Wl,-z,now",
                        os.path.join(build_dir, "fake_hal.o"),
                        harness, *objs, "-lm", "-o", exe],
                       capture_output=True, text=True)
    return (exe, None) if p.returncode == 0 else (None, p.stderr)


def parse_stdout(text):
    """Returns (lines, ok). ok is False if any line is not valid JSON (e.g. cut off by a kill)."""
    out = []
    for x in text.splitlines():
        if not x.strip():
            continue
        try:
            out.append(json.loads(x))
        except ValueError:
            return out, False
    return out, True


def seccomp_mode(stderr):
    for x in (stderr or "").splitlines():
        if x.startswith("seccomp="):
            return x[len("seccomp="):].strip()
    return None


def classify(rc, lines, parsed_ok):
    done = any(x.get("done") for x in lines)
    if not parsed_ok:
        return "crashed", "partial or invalid JSON line in output"
    fatal = [x["fatal"] for x in lines if "fatal" in x]
    if fatal:
        return "crashed", "harness fatal: " + fatal[0]
    if rc == 0 and done:
        return "ok", ""
    if rc == -signal.SIGXCPU:
        return "timeout", "RLIMIT_CPU"
    # strict mode kills with SIGKILL, the filter fallback with SIGSYS
    if rc in (-signal.SIGKILL, -signal.SIGSYS) and not done:
        return "killed", ""
    return "crashed", "rc=%d" % rc


def run(exe, script):
    """Returns (status, snapshots, detail, seccomp_mode) classified per ARCHITECTURE 8.3."""
    try:
        p = subprocess.run([exe], input=script, capture_output=True, text=True, timeout=5,
                           preexec_fn=limits)
    except subprocess.TimeoutExpired as e:
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else e.stderr
        return "timeout", [], "wall timeout", seccomp_mode(err)
    lines, ok = parse_stdout(p.stdout)
    st, detail = classify(p.returncode, lines, ok)
    return st, [x for x in lines if "t" in x], detail, seccomp_mode(p.stderr)


def parser_selftest():
    lines, ok = parse_stdout('{"t":0}\n{"t":50,"s":{"te')
    assert not ok and classify(-signal.SIGKILL, lines, ok)[0] == "crashed"
    lines, ok = parse_stdout('{"t":0}\n{"done":true}\n')
    assert ok and classify(0, lines, ok)[0] == "ok"


# ---- assertions: each returns None on success or a failure string -------------------------

def at(snaps, t):
    for s in snaps:
        if s["t"] == t:
            return s
    return None


def a_threshold(st, sn):
    if st != "ok":
        return "status " + st
    for s in sn:
        want = 1 if s["t"] >= 500 else 0
        if s["a"]["fan"] != want:
            return "t=%d fan=%d want %d" % (s["t"], s["a"]["fan"], want)
        if s["err"]:
            return "unexpected err %r" % s["err"]
    if at(sn, 500) is None or at(sn, 950) is None or at(sn, 1000) is not None:
        return "wrong snapshot range"
    if at(sn, 500)["s"]["temp"] != 30.1:
        return "s.temp not 30.1 at t=500"
    return None


def a_hysteresis(st, sn):
    if st != "ok":
        return "status " + st
    for s in sn:
        want = 0 if s["t"] >= 400 else 1
        if s["a"]["fan"] != want:
            return "t=%d fan=%d want %d" % (s["t"], s["a"]["fan"], want)
    return None


def a_display(st, sn):
    if st != "ok":
        return "status " + st
    for s in sn:
        if s["d"] != ["Temp: 30.1C", "", "", ""]:
            return "t=%d d=%r" % (s["t"], s["d"])
    return None


def a_timer(st, sn):
    if st != "ok":
        return "status " + st
    on = [s["t"] for s in sn if s["a"]["buzzer"] == 1]
    if on != [100, 150, 200, 250]:
        return "buzzer on at %r, want [100, 150, 200, 250]" % on
    return None


def a_unknown(st, sn):
    if st != "ok":
        return "status " + st
    want = ['UNKNOWN_CHANNEL: readFloat("tmp")']
    for s in sn:
        if s["err"] != want:
            return "t=%d err=%r" % (s["t"], s["err"])
    return None if sn else "no snapshots"


def expect_status(want):
    return lambda st, sn: None if st == want else "status %s, want %s" % (st, want)


def a_overflow(st, sn):
    return None if st == "crashed" else "status %s, want crashed" % st


CASES = [
    ("threshold", a_threshold),
    ("hysteresis", a_hysteresis),
    ("display", a_display),
    ("timer", a_timer),
    ("unknown_channel", a_unknown),
    ("busy_loop", expect_status("timeout")),
    ("fopen_after", expect_status("killed")),
    ("stack_smash", expect_status("killed")),
    # snapshot buffer forced to 64 bytes: must report a fatal error, not print truncated JSON
    ("snapshot_overflow", a_overflow, {"logic": "threshold", "defs": ["-DFL_OUT_CAP=64"],
                           "detail": "snapshot overflow"}),
]


def main():
    parser_selftest()
    build_dir = sys.argv[1]
    failed = 0
    for name, check, *rest in CASES:
        opts = rest[0] if rest else {}
        exe, err = build(build_dir, name, opts.get("logic"), opts.get("defs", ()))
        if exe is None:
            print("FAIL %s: build: %s" % (name, err.strip().splitlines()[0] if err else "?"))
            failed += 1
            continue
        with open(os.path.join(HERE, "scripts", name + ".txt")) as f:
            script = f.read()
        st, sn, detail, mode = run(exe, script)
        if mode not in ("strict", "filter"):
            print("FAIL %s: seccomp mode not reported or unknown (%r)" % (name, mode))
            failed += 1
            continue
        msg = check(st, sn)
        want = opts.get("detail")
        if not msg and want and want not in detail:
            msg = "detail %r lacks %r" % (detail, want)
        if msg:
            print("FAIL %s: %s %s [seccomp=%s]" % (name, msg, detail, mode))
            failed += 1
        else:
            print("PASS %s (%s, seccomp=%s)" % (name, st, mode))
    print("%d case(s) failed" % failed if failed else "all cases passed")
    return 1 if failed else 0


sys.exit(main())
