# Keep native sysroot preprocessing/relocation, but discard compressed sstate
# archives on hosted runners. SSTATE_SKIP_CREATION would skip the hooks too.
SSTATEPOSTCREATEFUNCS:append = " pantavisor_ci_discard_sstate"

python pantavisor_ci_discard_sstate() {
    import os
    archive = d.getVar('SSTATE_PKG')
    for path in (archive, archive + '.sig'):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
}
