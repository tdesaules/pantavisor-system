#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <json-c/json.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define HEADER "# GRUB Environment Block\n"
#ifndef TRACE_ROOT
#define TRACE_ROOT "/mnt/ubuntu-trial"
#endif
#define MAIN_ENV TRACE_ROOT "/boot/grub/grubenv"
#define PV_ENV TRACE_ROOT "/boot/pantavisor/state/grubenv"
#define BUDGET_ENV TRACE_ROOT "/boot/pantavisor/state/boot-budget"
#define ENTRY "pantavisor-persistent"

static int env_edit(const char *path, const char *key, const char *value, int main_env)
{
    char old[1025] = {0}, next[1024];
    struct stat st;
    int fd = open(path, O_RDWR | O_CLOEXEC | O_NOFOLLOW | O_SYNC);
    if (fd < 0) return 2;
    int rc = 2;
    if (flock(fd, LOCK_EX) || fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size != 1024 ||
        read(fd, old, 1024) != 1024 || memcmp(old, HEADER, strlen(HEADER))) goto out;
    memset(next, '#', sizeof(next));
    memcpy(next, HEADER, strlen(HEADER));
    size_t used = strlen(HEADER);
    char *line = old + used;
    while (*line && *line != '#') {
        char *end = strchr(line, '\n');
        if (!end) goto out;
        size_t length = (size_t)(end - line);
        *end = 0;
        if (!strncmp(line, key, strlen(key)) && line[strlen(key)] == '=') {
            const char *current = line + strlen(key) + 1;
            if (main_env && *current && strcmp(current, ENTRY)) {
                fputs("Another GRUB next_entry is queued; refusing to replace it\n", stderr);
                goto out;
            }
        } else {
            if (used + length + 1 >= sizeof(next)) goto out;
            memcpy(next + used, line, length); used += length; next[used++] = '\n';
        }
        line = end + 1;
    }
    if (value) {
        size_t length = strlen(key) + strlen(value) + 2;
        if (used + length >= sizeof(next)) goto out;
        memcpy(next + used, key, strlen(key)); used += strlen(key);
        next[used++] = '=';
        memcpy(next + used, value, strlen(value)); used += strlen(value);
        next[used++] = '\n';
    }
    if (pwrite(fd, next, sizeof(next), 0) != sizeof(next) || fsync(fd)) goto out;
    rc = 0;
out:
    close(fd);
    return rc;
}

static const char *field(struct json_object *obj, const char *key)
{
    struct json_object *value = NULL;
    return json_object_object_get_ex(obj, key, &value) ? json_object_get_string(value) : NULL;
}

static int health(const char *path)
{
    if (strlen(path) >= sizeof(((struct sockaddr_un *)0)->sun_path)) return 2;
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return 2;
    struct timeval timeout = {.tv_sec = 2};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    strcpy(address.sun_path, path);
    const char request[] = "GET /containers HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n";
    char response[65536] = {0};
    size_t used = 0;
    int rc = 2;
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) ||
        write(fd, request, sizeof(request) - 1) != sizeof(request) - 1) goto out;
    while (used < sizeof(response) - 1) {
        ssize_t n = read(fd, response + used, sizeof(response) - 1 - used);
        if (n == 0) break;
        if (n < 0) goto out;
        used += (size_t)n;
    }
    int status = 0;
    if (sscanf(response, "HTTP/%*s %d", &status) != 1 || status != 200) goto out;
    char *body = strstr(response, "\r\n\r\n");
    if (!body) goto out;
    struct json_object *array = json_tokener_parse(body + 4);
    if (!array || !json_object_is_type(array, json_type_array)) {
        if (array) json_object_put(array);
        goto out;
    }
    int ready = 1, core = 0;
    for (size_t i = 0; i < json_object_array_length(array); i++) {
        struct json_object *item = json_object_array_get_idx(array, i);
        const char *name = field(item, "name"), *state = field(item, "status");
        const char *goal = field(item, "status_goal"), *stopped = field(item, "user_stopped");
        if (name && !strcmp(name, "core") && state && !strcmp(state, "READY")) core = 1;
        if (stopped && !strcmp(stopped, "true")) continue;
        if (!state || !goal || (strcmp(state, goal) && !(strcmp(state, "READY") == 0 && strcmp(goal, "STARTED") == 0))) ready = 0;
    }
    struct timespec now;
    clock_gettime(CLOCK_BOOTTIME, &now);
    printf("response_at=%ld\nready=%d\n", now.tv_sec, ready && core);
    json_object_put(array);
    rc = 0;
out:
    close(fd);
    return rc;
}

int main(int argc, char **argv)
{
    if (argc == 3 && !strcmp(argv[1], "health")) return health(argv[2]);
    if (argc == 2 && !strcmp(argv[1], "stop-diagnostics")) {
        FILE *f = fopen("/ventuno-trial/diagnostic.pid", "r");
        long pid = 0;
        if (f) { if (fscanf(f, "%ld", &pid) != 1) pid = 0; fclose(f); }
        if (pid > 1 && pid <= 4194304 && getpgid((pid_t)pid) == (pid_t)pid)
            kill(-(pid_t)pid, SIGTERM);
        return 0;
    }
    if (argc != 2 || access("/etc/ventuno-autonomous", F_OK)) return 2;
    if (!strcmp(argv[1], "arm")) {
        if (env_edit(BUDGET_ENV, "ventuno_recovery", "0", 0)) return 2;
        return env_edit(MAIN_ENV, "next_entry", ENTRY, 1);
    }
    if (!strcmp(argv[1], "clear")) {
        /* Latch recovery across cold boots, even when Pantavisor is default. */
        if (env_edit(BUDGET_ENV, "ventuno_recovery", "1", 0)) return 2;
        return env_edit(MAIN_ENV, "next_entry", NULL, 1);
    }
    if (!strcmp(argv[1], "clear-attempt")) return env_edit(PV_ENV, "ventuno_attempt", NULL, 0);
    if (!strcmp(argv[1], "stable")) {
        if (env_edit(BUDGET_ENV, "ventuno_boots", "0", 0)) return 2;
        return env_edit(BUDGET_ENV, "ventuno_recovery", "0", 0);
    }
    return 2;
}
