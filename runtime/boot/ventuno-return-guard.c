#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/watchdog.h>
#include <linux/loop.h>
#include <signal.h>
#include <sched.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/reboot.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define DEFAULT_SECONDS 180
#define MAX_SECONDS 1800
#define WATCHDOG_SECONDS 30
#ifndef TRACE_ROOT
#define TRACE_ROOT "/mnt/ubuntu-trial"
#endif
static int autonomous_mode;

static long seconds_now(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_BOOTTIME, &t))
        _exit(2);
    return t.tv_sec;
}

static int value(const char *line, const char *key, char *out, size_t cap)
{
    const char *p = line;
    while ((p = strstr(p, key))) {
        if (p != line && !isspace((unsigned char)p[-1])) {
            p++;
            continue;
        }
        p += strlen(key);
        size_t n = strcspn(p, " \t\r\n");
        if (!n || n >= cap)
            return 0;
        memcpy(out, p, n);
        out[n] = 0;
        return 1;
    }
    return 0;
}

static int trial_id(const char *line, char id[33])
{
    if (!value(line, autonomous_mode ? "ventuno_install=" : "ventuno_trial=", id, 33) || strlen(id) != 32)
        return 0;
    for (int i = 0; i < 32; i++)
        if (!isxdigit((unsigned char)id[i]))
            return 0;
    return 1;
}

static int deadline_seconds(const char *line)
{
    char text[16], *end;
    if (!value(line, "ventuno_return_seconds=", text, sizeof(text)))
        return DEFAULT_SECONDS;
    long n = strtol(text, &end, 10);
    return *end || n < 15 || n > MAX_SECONDS ? DEFAULT_SECONDS : (int)n;
}

static void hard_deadline(int signo)
{
    (void)signo;
    /* No dependency on proc, Pantavisor, a logger or a cooperating main loop. */
    reboot(RB_AUTOBOOT);
    _exit(255); /* A failed PID 1 reboot also triggers the kernel panic policy. */
}

static void event(const char *format, ...)
{
    char message[512];
    va_list args;
    va_start(args, format);
    vsnprintf(message, sizeof(message), format, args);
    va_end(args);
    dprintf(STDERR_FILENO, "[VENTUNO] %s\n", message);
    int fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
    if (fd >= 0) {
        dprintf(fd, "<5>[VENTUNO] %s\n", message);
        close(fd);
    }
    fd = open("/run/ventuno-supervisor.log", O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
    if (fd >= 0) {
        dprintf(fd, "uptime=%ld %s\n", seconds_now(), message);
        close(fd);
    }
}

static void initial_mount(const char *path, const char *type, const char *options)
{
    if (mkdir(path, 0755) && errno != EEXIST)
        event("mkdir %s failed: %s", path, strerror(errno));
    if (mount(type, path, type, 0, options) && errno != EBUSY)
        event("mount %s failed: %s", path, strerror(errno));
}

static int read_text(const char *path, char *buffer, size_t size)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return 0;
    ssize_t n = read(fd, buffer, size - 1);
    close(fd);
    if (n < 0)
        return 0;
    buffer[n] = 0;
    return 1;
}

static int marker_matches(const char *directory, const char *id)
{
    char path[256], text[64];
    snprintf(path, sizeof(path), "%s/%s", directory, autonomous_mode ? "install-id" : "trial-id");
    if (!read_text(path, text, sizeof(text)))
        return 0;
    text[strcspn(text, "\r\n")] = 0;
    return !strcmp(text, id);
}

static int loop_autoclear(int number)
{
    char path[64];
    snprintf(path, sizeof(path), "/dev/loop%d", number);
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0) return 0;
    struct loop_info64 info;
    int ok = ioctl(fd, LOOP_GET_STATUS64, &info) == 0;
    if (ok && !(info.lo_flags & LO_FLAGS_AUTOCLEAR)) {
        info.lo_flags |= LO_FLAGS_AUTOCLEAR;
        ok = ioctl(fd, LOOP_SET_STATUS64, &info) == 0;
    }
    close(fd);
    return ok;
}

static int arm_storage_release(const char *id)
{
    char line[2048], source[256], target[256], suffix[128];
    int mounted = 0;
    FILE *f = fopen("/proc/1/mounts", "r");
    if (!f) return 0;
    while (fgets(line, sizeof(line), f)) {
        if (sscanf(line, "%255s %255s", source, target) == 2 &&
            !strcmp(source, "/dev/loop32") && !strcmp(target, "/storage")) mounted = 1;
    }
    fclose(f);
    if (!mounted) return 0;
    if (autonomous_mode) snprintf(suffix, sizeof(suffix), "/boot/pantavisor/pvstore.ext4");
    else snprintf(suffix, sizeof(suffix), "/%s/pvstore.ext4", id);
    if (!read_text("/sys/class/block/loop32/loop/backing_file", line, sizeof(line)) || !strstr(line, suffix)) return 0;
    if (!loop_autoclear(32)) return 0;
    /* Pantavisor retains each image loop fd through its mount lifecycle.
     * AUTOCLEAR releases remaining references when those owners close, also
     * on update-triggered reboot before the guardian deadline. */
    for (int number = 64; number < 128; number++) {
        char path[128];
        snprintf(path, sizeof(path), "/sys/class/block/loop%d/loop/backing_file", number);
        if (read_text(path, line, sizeof(line)) &&
            (!strncmp(line, "/storage/trails/", 16) || !strncmp(line, "/storage/objects/", 17)))
            loop_autoclear(number);
    }
    return 1;
}

static void diagnostic_worker(const char *id, const char *device)
{
    setsid();
    mkdir("/mnt", 0755);
    mkdir(TRACE_ROOT, 0755);
    for (int i = 0; access(device, F_OK) && i < 20; i++)
        sleep(1);
    if (mount(device, TRACE_ROOT, "ext4", MS_RDONLY, "noload")) {
        event("EARLY_LOG_MOUNT_FAILED: %s", strerror(errno));
        _exit(3);
    }
    char directory[192], path[512];
    if (autonomous_mode) snprintf(directory, sizeof(directory), TRACE_ROOT "/boot/pantavisor");
    else snprintf(directory, sizeof(directory), TRACE_ROOT "/boot/pantavisor-diagnostic/%s", id);
    if (!marker_matches(directory, id)) {
        event("EARLY_LOG_MARKER_MISMATCH");
        umount(TRACE_ROOT);
        _exit(4);
    }
    if (umount(TRACE_ROOT) || mount(device, TRACE_ROOT, "ext4", 0, NULL)) {
        event("EARLY_LOG_RW_MOUNT_FAILED: %s", strerror(errno));
        _exit(5);
    }
    if (!marker_matches(directory, id)) {
        umount(TRACE_ROOT);
        _exit(6);
    }
    char boot_id[64] = "unknown";
    read_text("/proc/sys/kernel/random/boot_id", boot_id, sizeof(boot_id));
    boot_id[strcspn(boot_id, "\r\n")] = 0;
    if (autonomous_mode) {
        snprintf(path, sizeof(path), "%s/logs", directory);
        if (mkdir(path, 0700) && errno != EEXIST) _exit(7);
        snprintf(path, sizeof(path), "%s/logs/%s.log", directory, boot_id);
    } else snprintf(path, sizeof(path), "%s/trial.log", directory);
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_APPEND | O_CLOEXEC, 0600);
    if (fd < 0) {
        event("EARLY_LOG_CREATE_FAILED: %s", strerror(errno));
        umount(TRACE_ROOT);
        _exit(7);
    }
    struct utsname info;
    uname(&info);
    dprintf(fd, "TRIAL_STARTED=%s\nTRIAL_BOOT_ID=%s\nPID1_NAME=ventuno-init\n"
            "KERNEL_RELEASE=%s\nBOOTSTRAP_STAGE=EARLY_LOG_READY\n", id, boot_id, info.release);
    if (autonomous_mode) dprintf(fd, "AUTONOMOUS_BOOT=1\n");
    fsync(fd);
    int ready = open("/run/ventuno-trace-ready", O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
    if (ready >= 0)
        close(ready);
    event("EARLY_LOG_READY: persistent marker written before Pantavisor");
    dup2(fd, STDOUT_FILENO);
    dup2(fd, STDERR_FILENO);
    close(fd);
    execl("/usr/sbin/ventuno-trial-diagnostics", "ventuno-trial-diagnostics", id, "--mounted", (char *)NULL);
    dprintf(STDERR_FILENO, "DIAGNOSTIC_EXEC_FAILED=%s\n", strerror(errno));
    fsync(STDOUT_FILENO);
    close(STDOUT_FILENO);
    close(STDERR_FILENO);
    umount(TRACE_ROOT);
    _exit(127);
}

static void status_file(long elapsed, int deadline, int wd, int timeout, pid_t pv, int pv_exit, int diag_exit, pid_t net, int net_exit, int pid1_mode)
{
    FILE *f = fopen("/run/ventuno-guard.status.tmp", "w");
    if (!f)
        return;
    fprintf(f, "supervisor_version=%d\nphase=%s\nelapsed=%ld\nreturn_after=%d\n"
            "watchdog=%s\nwatchdog_timeout=%d\npantavisor_pid=%ld\npantavisor_state=%s\n"
            "pantavisor_exit=%d\ndiagnostic_exit=%d\nnetwork_pid=%ld\nnetwork_exit=%d\n", autonomous_mode ? 5 : pid1_mode ? 4 : 2,
            autonomous_mode ? "autonomous" : "diagnostic", elapsed, autonomous_mode ? 0 : deadline,
            wd >= 0 ? "armed" : "unavailable", timeout, (long)pv,
            pv > 0 ? "running" : pv_exit == -1 ? "not-started" : "exited", pv_exit, diag_exit, (long)net, net_exit);
    fclose(f);
    rename("/run/ventuno-guard.status.tmp", "/run/ventuno-guard.status");
    if (pid1_mode) {
        char status[2048], name[64] = "unknown", exe[256] = "unknown";
        read_text("/proc/1/comm", name, sizeof(name));
        name[strcspn(name, "\r\n")] = 0;
        ssize_t n = readlink("/proc/1/exe", exe, sizeof(exe) - 1);
        if (n >= 0) exe[n] = 0;
        if (read_text("/run/ventuno-guard.status", status, sizeof(status))) {
            f = fopen("/ventuno-trial/guard.status.tmp", "w");
            if (f) {
                fprintf(f, "%shost_pid1_name=%s\nhost_pid1_exe=%s\nguardian_pid=%ld\n", status, name, exe, (long)getpid());
                fclose(f);
                rename("/ventuno-trial/guard.status.tmp", "/ventuno-trial/guard.status");
            }
        }
    }
}

static void pid1_handoff(const char *id, const char *cmdline, long start, int deadline)
{
    /* Keep the early PID 1 alarm until a separately armed guardian and the
     * checked file-backed store support are ready. The guardian remains in
     * the original mount namespace; Pantavisor will mount its own /run. */
    while (seconds_now() - start < deadline) {
        if (seconds_now() - start >= 5 &&
            !access("/run/ventuno-trace-ready", F_OK) &&
            !access("/run/ventuno-guardian-ready", F_OK)) {
            char fault[8];
            if (value(cmdline, "ventuno_pid1_test_stall=", fault, sizeof(fault)) && !strcmp(fault, "1")) {
                alarm(0);
                event("PID1_BOOTSTRAP_STALLED: only the independent guardian can return");
                for (;;) pause();
            }
            if (unshare(CLONE_NEWNS) || mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL))
                hard_deadline(0);
            event("PANTAVISOR_PID1_HANDOFF: exec with guardian armed");
            alarm(0);
            execl("/usr/sbin/ventuno-runtime", "ventuno-runtime", id, (char *)NULL);
            hard_deadline(0);
        }
        sleep(1);
    }
    hard_deadline(0);
}

static int self_test(void)
{
    char id[33];
    if (!trial_id("x=1 ventuno_trial=0123456789abcdef0123456789abcdef panic=10", id) ||
        strcmp(id, "0123456789abcdef0123456789abcdef") ||
        trial_id("not_ventuno_trial=0123456789abcdef0123456789abcdef", id) ||
        trial_id("ventuno_trial=short", id) ||
        trial_id("ventuno_trial=0123456789abcdef0123456789abcdeX", id) ||
        trial_id("ventuno_trial=0123456789abcdef0123456789abcdef0", id) ||
        deadline_seconds("ventuno_return_seconds=45") != 45 ||
        deadline_seconds("ventuno_return_seconds=0") != DEFAULT_SECONDS ||
         deadline_seconds("ventuno_return_seconds=900") != 900 ||
         deadline_seconds("ventuno_return_seconds=1800") != MAX_SECONDS ||
         deadline_seconds("ventuno_return_seconds=1801") != DEFAULT_SECONDS ||
        deadline_seconds("ventuno_return_seconds=45oops") != DEFAULT_SECONDS)
        return 1;
    autonomous_mode = 1;
    if (!trial_id("ventuno_install=0123456789abcdef0123456789abcdef", id) ||
        trial_id("ventuno_trial=0123456789abcdef0123456789abcdef", id)) return 1;
    autonomous_mode = 0;
    puts("PASS: supervisor-v4 token/deadline validation; default deadline=180s; maximum=1800s; autonomous-v5 token validation");
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--self-test"))
        return self_test();
    if (getpid() != 1 || geteuid() != 0) {
        fputs("Supervisor must be PID 1 in the diagnostic initramfs\n", stderr);
        return 2;
    }
    prctl(PR_SET_NAME, "ventuno-init", 0, 0, 0);
    struct sigaction action = {0};
    action.sa_handler = hard_deadline;
    sigemptyset(&action.sa_mask);
    sigaction(SIGALRM, &action, NULL);
    alarm(DEFAULT_SECONDS + 10); /* Armed before mounts and all child processes. */
    long start = seconds_now();
    autonomous_mode = !access("/etc/ventuno-autonomous", F_OK);
    int pid1_mode = !access("/etc/ventuno-pid1", F_OK);
    event("SUPERVISOR_V2_STARTED: autonomous PID 1");
    initial_mount("/proc", "proc", NULL);
    initial_mount("/sys", "sysfs", NULL);
    initial_mount("/dev", "devtmpfs", pid1_mode ? "size=10240k,mode=0755" : "mode=0755");
    initial_mount("/dev/pts", "devpts", NULL);
    initial_mount("/run", "tmpfs", "mode=0755");
    initial_mount("/tmp", "tmpfs", "mode=1777");
    int console = open("/dev/console", O_RDWR | O_CLOEXEC);
    if (console >= 0) {
        dup2(console, STDIN_FILENO);
        dup2(console, STDOUT_FILENO);
        dup2(console, STDERR_FILENO);
        if (console > STDERR_FILENO)
            close(console);
    }
    event("EARLY_MOUNTS_READY");
    char cmdline[8192] = {0}, id[33] = {0};
    if (!read_text("/proc/cmdline", cmdline, sizeof(cmdline)) || !trial_id(cmdline, id)) {
        event("INVALID_TRIAL_TOKEN: returning to default system");
        hard_deadline(0);
    }
    int deadline = autonomous_mode ? DEFAULT_SECONDS : deadline_seconds(cmdline);
    long remaining = deadline + 10 - (seconds_now() - start);
    alarm(remaining > 0 ? (unsigned)remaining : 1);
    if (autonomous_mode) event("AUTONOMOUS_TOKEN_VALID: startup budget=%d seconds; no runtime deadline", deadline);
    else event("TRIAL_TOKEN_VALID: return in %d seconds", deadline);
    char fault[8];
    if (value(cmdline, "ventuno_test_pause=", fault, sizeof(fault)) && !strcmp(fault, "1")) {
        event("FAULT_INJECTION_PAUSE_BEFORE_CHILDREN: waiting for hard deadline");
        for (;;)
            pause();
    }

    if (pid1_mode) {
        initial_mount("/ventuno-trial", "tmpfs", "mode=0755");
        if (autonomous_mode) mkdir("/ventuno-trial/control", 0700);
        FILE *f = fopen("/run/ventuno-runtime.pid", "w");
        if (!f) hard_deadline(0);
        fputs("1\n", f); fclose(f);
        pid_t guardian = fork();
        if (guardian < 0) hard_deadline(0);
        if (guardian > 0) pid1_handoff(id, cmdline, start, deadline);
        prctl(PR_SET_NAME, "ventuno-guard", 0, 0, 0);
        setsid();
        /* alarm() is not inherited across fork. */
        remaining = deadline + 10 - (seconds_now() - start);
        alarm(autonomous_mode ? 0 : remaining > 0 ? (unsigned)remaining : 1);
        int ready = open("/run/ventuno-guardian-ready", O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
        if (ready < 0) hard_deadline(0);
        close(ready);
        event("PID1_GUARDIAN_ARMED: pid=%ld", (long)getpid());
    }

    pid_t loader = fork();
    if (loader == 0) {
        execl("/sbin/modprobe", "modprobe", "qcom_wdt", (char *)NULL);
        _exit(127);
    }
    char device[64] = "/dev/mmcblk0p71", requested[64];
    if (value(cmdline, "ventuno_trace_device=", requested, sizeof(requested)) && !strcmp(requested, "/dev/vda"))
        strcpy(device, requested); /* Virtual disk used by the QEMU integration test. */
    pid_t diagnostic = fork();
    if (diagnostic == 0)
        diagnostic_worker(id, device);
    if (pid1_mode) {
        FILE *f = fopen("/ventuno-trial/diagnostic.pid", "w");
        if (f) { fprintf(f, "%ld\n", (long)diagnostic); fclose(f); }
    }

    int wd = -1, timeout = WATCHDOG_SECONDS, pv_exit = -1, diag_exit = -1;
    pid_t pv = pid1_mode ? 1 : 0;
    int pv_started = pid1_mode;
    pid_t net = 0;
    int net_started = pid1_mode, net_exit = -1;
    int cleanup_started = 0;
    int storage_watcher_started = 0;
    int health_started = 0, ready_once = 0, stability_marked = 0;
    long last_response = start, last_ready = start;
    while (autonomous_mode || seconds_now() - start < deadline) {
        if (wd < 0) {
            wd = open("/dev/watchdog0", O_WRONLY | O_CLOEXEC);
            if (wd >= 0) {
                if (ioctl(wd, WDIOC_SETTIMEOUT, &timeout) < 0)
                    event("WATCHDOG_SETTIMEOUT_FAILED: %s", strerror(errno));
                if (ioctl(wd, WDIOC_GETTIMEOUT, &timeout) < 0)
                    event("WATCHDOG_GETTIMEOUT_FAILED: %s", strerror(errno));
                event("WATCHDOG_ARMED: timeout=%d", timeout);
            }
        }
        if (wd >= 0 && write(wd, "\0", 1) != 1)
            event("WATCHDOG_HEARTBEAT_FAILED: %s", strerror(errno));
        if (autonomous_mode) {
            if (!health_started && !access("/run/ventuno-trace-ready", F_OK)) {
                health_started = 1;
                pid_t health = fork();
                if (health == 0) {
                    setsid();
                    execl("/usr/sbin/ventuno-health-watch", "ventuno-health-watch", (char *)NULL);
                    _exit(127);
                }
                event("HEALTH_WATCHER_STARTED: pid=%ld", (long)health);
            }
            char health[256], stamp[32], ready[8];
            if (read_text("/ventuno-trial/health.status", health, sizeof(health)) &&
                value(health, "response_at=", stamp, sizeof(stamp))) {
                char *end;
                long observed = strtol(stamp, &end, 10);
                if (!*end && observed >= start && observed <= seconds_now()) last_response = observed;
                if (value(health, "ready=", ready, sizeof(ready)) && !strcmp(ready, "1")) {
                    ready_once = 1;
                    last_ready = last_response;
                }
            }
            if ((!ready_once && seconds_now() - start >= DEFAULT_SECONDS) ||
                (ready_once && (seconds_now() - last_response > 45 || seconds_now() - last_ready > 120))) {
                event("AUTONOMOUS_HEALTH_FAILURE: returning to Ubuntu (ready_once=%d)", ready_once);
                int next = open("/ventuno-trial/control/next", O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
                if (next >= 0) { write(next, "ubuntu\n", 7); close(next); }
                alarm(10);
                pid_t recovery = fork();
                if (recovery == 0) {
                    execl("/bin/nsenter", "nsenter", "-t", "1", "-m", "--", "/bin/sh", "-c",
                          "/usr/sbin/ventuno-bootctl clear; pvcontrol -s /run/pantavisor/pv/pv-ctrl cmd reboot", (char *)NULL);
                    _exit(127);
                }
                if (recovery > 0) waitpid(recovery, NULL, 0);
                sync();
                /* Give the runtime and storage watcher the remainder of the
                 * bounded grace period; SIGALRM still forces a stuck reboot. */
                for (;;) pause();
            }
            if (!stability_marked && ready_once && seconds_now() - start >= 120 && seconds_now() - last_ready <= 10) {
                stability_marked = 1;
                pid_t stable = fork();
                if (stable == 0) {
                    execl("/bin/nsenter", "nsenter", "-t", "1", "-m", "--", "/usr/sbin/ventuno-bootctl", "stable", (char *)NULL);
                    _exit(127);
                }
                event("AUTONOMOUS_STABILITY_MARK_STARTED: pid=%ld", (long)stable);
            }
        }
        if (pid1_mode && arm_storage_release(id) && !storage_watcher_started) {
            storage_watcher_started = 1;
            pid_t watcher = fork();
            if (watcher == 0) {
                setsid();
                int fd = open("/run/ventuno-storage-watch.log", O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
                if (fd >= 0) { dup2(fd, 1); dup2(fd, 2); if (fd > 2) close(fd); }
                execl("/usr/sbin/ventuno-storage-watch", "ventuno-storage-watch", id, (char *)NULL);
                _exit(127);
            }
            event("STORAGE_WATCHER_STARTED: pid=%ld", (long)watcher);
        }
        if (!autonomous_mode && !cleanup_started && pv > 0 && !access("/etc/ventuno-embedded.config", F_OK) &&
            seconds_now() - start >= deadline - 20) {
            cleanup_started = 1;
            pid_t cleanup = fork();
            if (cleanup == 0) {
                setsid();
                int fd = open("/run/ventuno-runtime-cleanup.log", O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
                if (fd >= 0) { dup2(fd, 1); dup2(fd, 2); if (fd > 2) close(fd); }
                const char *cleanup_script = pid1_mode ? "/usr/sbin/ventuno-pid1-shutdown" : "/usr/sbin/ventuno-runtime-cleanup";
                execl(cleanup_script, cleanup_script, id, (char *)NULL);
                _exit(127);
            }
            event("RUNTIME_CLEANUP_STARTED: pid=%ld", (long)cleanup);
        }
        if (!net_started && !access("/etc/ventuno-network.conf", F_OK) &&
            !access("/run/ventuno-trace-ready", F_OK)) {
            net_started = 1;
            net = fork();
            if (net == 0) {
                setsid();
                int fd = open("/run/ventuno-network.log", O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
                if (fd >= 0) {
                    dup2(fd, STDOUT_FILENO);
                    dup2(fd, STDERR_FILENO);
                    if (fd > STDERR_FILENO)
                        close(fd);
                }
                execl("/usr/sbin/ventuno-network", "ventuno-network", (char *)NULL);
                _exit(127);
            }
            event("NETWORK_CHILD_STARTED: pid=%ld", (long)net);
        }
        if (!pv_started && seconds_now() - start >= 5 && !access("/run/ventuno-trace-ready", F_OK)) {
            pv_started = 1;
            pv = fork();
            if (pv == 0) {
                setsid();
                int fd = open("/run/pantavisor-child.log", O_WRONLY | O_CREAT | O_CLOEXEC, 0600);
                if (fd >= 0) {
                    dup2(fd, STDOUT_FILENO);
                    dup2(fd, STDERR_FILENO);
                    close(fd);
                }
                if (!access("/etc/ventuno-embedded.config", F_OK)) {
                    if (unshare(CLONE_NEWNS) || mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL))
                        _exit(126);
                    execl("/usr/sbin/ventuno-runtime", "ventuno-runtime", id, (char *)NULL);
                } else {
                    execl("/usr/bin/pantavisor", "pantavisor", "--config", "/etc/ventuno-supervised.config", "pv_standalone", (char *)NULL);
                }
                _exit(127);
            }
            FILE *pidfile = fopen("/run/ventuno-runtime.pid", "w");
            if (pidfile) { fprintf(pidfile, "%ld\n", (long)pv); fclose(pidfile); }
            event("PANTAVISOR_CHILD_STARTED: pid=%ld", (long)pv);
        }
        int status;
        pid_t reaped;
        while ((reaped = waitpid(-1, &status, WNOHANG)) > 0) {
            int code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
            if (reaped == diagnostic) {
                diagnostic = -1;
                FILE *f = fopen("/ventuno-trial/diagnostic.pid", "w");
                if (f) { fputs("0\n", f); fclose(f); }
                diag_exit = code;
                event("DIAGNOSTIC_EXIT: %d", code);
            }
            if (reaped == pv) {
                pv = -1;
                pv_exit = code;
                event("PANTAVISOR_CHILD_EXIT: %d", code);
            }
            if (reaped == net) {
                net = -1;
                net_exit = code;
                event("NETWORK_CHILD_EXIT: %d", code);
            }
        }
        status_file(seconds_now() - start, deadline, wd, timeout, pv, pv_exit, diag_exit, net, net_exit, pid1_mode);
        sleep(1);
    }
    event("RETURN_REQUESTED: deadline reached, rebooting to Ubuntu default");
    if (pid1_mode) event("PID1_GUARDIAN_FALLBACK: orderly reboot did not complete before deadline");
    if (diagnostic > 0)
        kill(-diagnostic, SIGTERM);
    if (pv > 0 && !pid1_mode)
        kill(-pv, SIGTERM);
    if (net > 0)
        kill(-net, SIGTERM);
    sleep(2);
    sync();
    reboot(RB_AUTOBOOT);
    event("REBOOT_FAILED: %s", strerror(errno));
    for (;;)
        pause(); /* Hard deadline and, when available, hardware watchdog remain armed. */
}
