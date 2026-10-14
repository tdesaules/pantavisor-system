SUMMARY = "Bounded diagnostic boot guard for the Ventuno Q lab"
LICENSE = "MIT"
FILESEXTRAPATHS:prepend := "${SYSTEM_REPO_ROOT}/runtime/boot:"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"
SRC_URI = "file://ventuno-return-guard.c file://ventuno-trial-diagnostics file://ventuno-network file://udhcpc.script file://ventuno-runtime file://ventuno-runtime-status file://ventuno-runtime-cleanup file://ventuno-pid1-shutdown file://ventuno-app-storage file://ventuno-storage-watch file://runtime-layout file://ventuno-bootctl.c file://ventuno-health-watch file://20-ventuno-boot-policy"
DEPENDS = "json-c"
S = "${WORKDIR}"
FILES:${PN}:append = " ${libdir}/ventuno"

do_compile() {
    ${CC} ${CFLAGS} ${LDFLAGS} -static -std=c11 -Wall -Wextra -Werror \
        ${S}/ventuno-return-guard.c -o ventuno-return-guard
    ${CC} ${CFLAGS} ${LDFLAGS} -std=c11 -Wall -Wextra -Werror \
        ${S}/ventuno-bootctl.c -ljson-c -o ventuno-bootctl
    ${CC} ${CFLAGS} ${LDFLAGS} -static -std=c11 -Wall -Wextra -Werror \
        -DTRACE_ROOT='"/mnt/pv-system"' ${S}/ventuno-return-guard.c -o ventuno-native-guard
    ${CC} ${CFLAGS} ${LDFLAGS} -std=c11 -Wall -Wextra -Werror \
        -DTRACE_ROOT='"/mnt/pv-system"' ${S}/ventuno-bootctl.c -ljson-c -o ventuno-native-bootctl
}

do_install() {
    install -d ${D}${sbindir}
    install -m 0755 ventuno-return-guard ${D}${sbindir}/
    install -m 0755 ventuno-native-guard ventuno-native-bootctl ${D}${sbindir}/
    install -m 0755 ${S}/ventuno-trial-diagnostics ${D}${sbindir}/
    install -m 0755 ${S}/ventuno-network ${D}${sbindir}/
    install -m 0755 ${S}/ventuno-runtime ${D}${sbindir}/
    install -m 0755 ${S}/ventuno-runtime-status ${D}${sbindir}/
    install -m 0755 ${S}/ventuno-runtime-cleanup ${D}${sbindir}/
    install -m 0755 ${S}/ventuno-pid1-shutdown ${D}${sbindir}/
    install -m 0755 ${S}/ventuno-app-storage ${D}${sbindir}/
    install -m 0755 ${S}/ventuno-storage-watch ${D}${sbindir}/
    install -m 0755 ventuno-bootctl ${S}/ventuno-health-watch ${D}${sbindir}/
    install -d ${D}${libdir}/ventuno
    install -m 0755 ${S}/udhcpc.script ${D}${libdir}/ventuno/
    install -m 0644 ${S}/runtime-layout ${D}${libdir}/ventuno/
    install -d ${D}${libdir}/pantavisor/pv/hooks/system.d
    # Match the directory metadata owned by the pinned pantavisor package.
    chmod 0644 ${D}${libdir}/pantavisor/pv/hooks/system.d
    install -m 0755 ${S}/20-ventuno-boot-policy ${D}${libdir}/pantavisor/pv/hooks/system.d/
}
FILES:${PN}:append = " ${libdir}/pantavisor/pv/hooks/system.d"
