#!/bin/sh
# Po instalaci / odinstalaci .deb i .rpm: obnoví menu aplikací, databázi typů souborů (.ffm)
# a cache ikon.
# Chyby se ignorují - jde jen o to, aby se položka v menu objevila hned.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v update-mime-database >/dev/null 2>&1; then
    update-mime-database /usr/share/mime || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
