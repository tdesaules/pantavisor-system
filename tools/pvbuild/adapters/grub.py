"""GRUB entry shared by the real installer and the ARM64 EFI integration test."""
import re
import uuid
from .revisions import BOOT_REVISION_PATTERN

ENTRY_ID = 'pantavisor-persistent'


def grub_default(root_uuid):
    """Run after 00_header; preserve grub-reboot and fail closed to Ubuntu."""
    root_uuid = str(uuid.UUID(root_uuid))
    return f'''# Persistent default with a recovery latch and rapid-boot budget.
if [ "$ventuno_force_ubuntu" = 1 ]; then
    set default=0
elif [ "$boot_once" != true ]; then
    set default=0
    insmod part_gpt
    insmod ext2
    insmod regexp
    search --no-floppy --fs-uuid --set=ventuno_root {root_uuid}
    set ventuno_boots=invalid
    set ventuno_recovery=0
    set pv_rev=invalid
    if load_env -f ($ventuno_root)/boot/pantavisor/state/boot-budget ventuno_boots ventuno_recovery; then
        if [ "$ventuno_recovery" = 0 ]; then
            if regexp '^[012]$' "$ventuno_boots"; then
                if load_env -f ($ventuno_root)/boot/pantavisor/state/grubenv pv_rev; then
                    if regexp '^{BOOT_REVISION_PATTERN}$' "$pv_rev"; then
                        if [ -f ($ventuno_root)/boot/pantavisor/kernel -a -f ($ventuno_root)/boot/pantavisor/initramfs -a -f ($ventuno_root)/boot/pantavisor/pvstore.ext4 ]; then
                            set default={ENTRY_ID}
                        fi
                    fi
                fi
            else
                echo 'PANTAVISOR_BOOT_BUDGET_EXHAUSTED'
            fi
        fi
    fi
fi
'''


def grub_entry(root_uuid, install_id, emulated=False, stall_bootstrap=False, fail_budget_write=False):
    root_uuid = str(uuid.UUID(root_uuid))
    if not re.fullmatch('[0-9a-f]{32}', install_id):
        raise ValueError('Invalid install ID')
    if stall_bootstrap and not emulated: raise ValueError('Fault injection is restricted to the QEMU fixture')
    if fail_budget_write and not emulated: raise ValueError('Fault injection is restricted to the QEMU fixture')
    console = 'console=ttyAMA0 ventuno_trace_device=/dev/vda' if emulated else 'console=tty0 console=ttyMSM0,115200n8 earlycon pcie_pme=nomsi video=LVDS-1:d'
    fault = ' ventuno_pid1_test_stall=1' if stall_bootstrap else ''
    budget_save = 'false' if fail_budget_write else 'save_env -f ($root)/boot/pantavisor/state/boot-budget ventuno_boots ventuno_recovery'
    text = f'''# Stable Pantavisor entry; Ubuntu recovery is menu entry zero.
menuentry 'Pantavisor persistent' --id {ENTRY_ID} {{
    insmod part_gpt
    insmod ext2
    insmod gzio
    insmod regexp
    search --no-floppy --fs-uuid --set=root {root_uuid}
    set ventuno_boots=0
    if ! load_env -f ($root)/boot/pantavisor/state/boot-budget ventuno_boots; then
        echo 'PANTAVISOR_BOOT_BUDGET_INVALID'
        sleep 3
        reboot
    fi
    if [ "$ventuno_boots" = 0 ]; then
        set ventuno_boots=1
    elif [ "$ventuno_boots" = 1 ]; then
        set ventuno_boots=2
    elif [ "$ventuno_boots" = 2 ]; then
        set ventuno_boots=3
    else
        echo 'PANTAVISOR_BOOT_BUDGET_EXHAUSTED'
        sleep 3
        reboot
    fi
    # Arm recovery before the kernel: even a bootstrap that never mounts the
    # support filesystem must return to Ubuntu on the next cold boot.
    set ventuno_recovery=1
    if ! {budget_save}; then
        echo 'PANTAVISOR_BOOT_BUDGET_WRITE_FAILED'
        sleep 3
        reboot
    fi
    set pv_rev=0
    set pv_try=
    set ventuno_attempt=
    if ! load_env -f ($root)/boot/pantavisor/state/grubenv pv_rev pv_try ventuno_attempt; then
        echo 'PANTAVISOR_BOOT_ENV_INVALID'
        sleep 3
        reboot
    fi
    if ! regexp '^{BOOT_REVISION_PATTERN}$' "$pv_rev"; then
        echo 'PANTAVISOR_REVISION_INVALID'
        sleep 3
        reboot
    fi
    set ventuno_selected="$pv_rev"
    if [ -n "$pv_try" ]; then
        if regexp '^{BOOT_REVISION_PATTERN}$' "$pv_try"; then
            if [ "$ventuno_attempt" != "$pv_try" ]; then
                set ventuno_attempt="$pv_try"
                if save_env -f ($root)/boot/pantavisor/state/grubenv ventuno_attempt; then
                    set ventuno_selected="$pv_try"
                fi
            fi
        fi
    else
        set ventuno_attempt=
        save_env -f ($root)/boot/pantavisor/state/grubenv ventuno_attempt
    fi
    echo "PANTAVISOR_SELECTED=$ventuno_selected"
    linux /boot/pantavisor/kernel rdinit=/init {console} loglevel=7 panic=10 ventuno_install={install_id} pv_rev=$ventuno_selected{fault}
    initrd /boot/pantavisor/initramfs
}}
'''
    # Do not reboot-loop if GRUB cannot persist its recovery latch. Re-enter
    # the menu with an exported override, honoring Ubuntu even with boot_once.
    return text.replace('        reboot\n', '''        set ventuno_force_ubuntu=1
        export ventuno_force_ubuntu
        set timeout=0
        configfile ($root)/boot/grub/grub.cfg
        halt
''')


def grubenv(values):
    content = '# GRUB Environment Block\n' + ''.join(f'{key}={value}\n' for key, value in values.items())
    encoded = content.encode()
    if len(encoded) >= 1024: raise ValueError('GRUB environment too large')
    return encoded + b'#' * (1024 - len(encoded))
