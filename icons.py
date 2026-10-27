"""Small, consistent outline icons drawn locally; no icon fonts or network assets."""
from functools import lru_cache
from PySide6.QtCore import Qt,QByteArray
from PySide6.QtGui import QIcon,QPixmap,QPainter
from PySide6.QtSvg import QSvgRenderer

SHAPES={
    'archive':'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M10 3v3h4v3h-4v3h4v4h-4v3"/>',
    'extract':'<path d="M3 8h18v13H3V8Zm0 0 2-5h14l2 5M12 10v8m-3-3 3 3 3-3"/>',
    'info':'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
    'hash':'<path d="M9 3 6 21M18 3l-3 18M3 9h18M2 15h18"/>',
    'status':'<circle cx="12" cy="12" r="7" fill="currentColor"/>',
    'activity':'<path d="M2 12h5l3-8 4 16 3-8h5"/>',
    'play':'<path d="m7 3 14 9-14 9V3Z"/>',
    'stop':'<rect x="5" y="5" width="14" height="14" rx="2"/>',
    'send':'<rect x="2" y="3" width="8" height="6" rx="1"/><rect x="14" y="15" width="8" height="6" rx="1"/><path d="M6 12v6h5m2-12h5v6m-3-3 3 3 3-3"/>',
    'new-folder':'<path d="M3 6h7l2 3h9v12H3V6Zm5 9h8m-4-4v8"/>',
    'lock':'<rect x="4" y="10" width="16" height="12" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 5v3"/>',
    'view':'<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    'download':'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    'upload':'<path d="M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5"/>',
    'new':'<path d="M14 3H5v18h14V8l-5-5v5h5M8 14h8m-4-4v8"/>',
    'edit':'<path d="m4 16-1 5 5-1L20 8l-4-4L4 16Zm10-10 4 4M3 3h8M3 7h5"/>',
    'copy':'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    'folder':'<path d="M3 6h7l2 3h9v12H3V6Z"/>',
    'server':'<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M6 6h1m-1 11h1m5-11h6m-6 11h6"/>',
    'terminal':'<rect x="2" y="3" width="20" height="18" rx="4"/><path d="m6 8 4 4-4 4m7 0h5"/>',
    'plus':'<path d="M12 4v16M4 12h16"/>',
    'rename':'<path d="m4 17-1 4 4-1L20 7l-4-4L4 17Zm10-12 4 4M13 21h8"/>',
    'move':'<path d="M3 6h7l2 3h9v12H3V6Zm4 9h10m-3-3 3 3-3 3"/>',
    'back':'<path d="m14 5-7 7 7 7M7 12h14"/>',
    'forward':'<path d="m10 5 7 7-7 7M3 12h14"/>',
    'up':'<path d="m5 10 7-7 7 7M12 3v18"/>',
    'down':'<path d="m5 14 7 7 7-7M12 3v18"/>',
    'home':'<path d="m2 11 10-8 10 8M5 9v12h14V9M10 21v-7h4v7"/>',
    'root':'<path d="M3 6h7l2 3h9v12H3V6Zm7 12 4-6"/>',
    'refresh':'<path d="M20 8a8 8 0 1 0 0 9M20 3v6h-6"/>',
    'connect':'<path d="M8 3v5m8-5v5M5 8h14v4a7 7 0 0 1-14 0V8Zm7 11v3"/>',
    'disconnect':'<path d="m3 3 18 18M8 3v3m8-3v5M5 8v4a7 7 0 0 0 10 6m4-6V8h-7M12 19v3"/>',
    'remove':'<path d="M5 7h14M9 7V3h6v4M7 7l1 14h8l1-14M10 11v6m4-6v6"/>',
    'close':'<path d="m6 6 12 12M6 18 18 6"/>',
}


@lru_cache(maxsize=128)
def icon(name,color='#b9c9db'):
    result=QIcon()
    for mode,stroke in ((QIcon.Mode.Normal,color),(QIcon.Mode.Disabled,'#5e6874')):
        svg=('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
             'color="'+stroke+'" stroke="'+stroke+'" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
             +SHAPES[name]+'</svg>')
        renderer=QSvgRenderer(QByteArray(svg.encode()))
        pixmap=QPixmap(40,40);pixmap.fill(Qt.GlobalColor.transparent)
        painter=QPainter(pixmap);renderer.render(painter);painter.end()
        pixmap.setDevicePixelRatio(2)
        result.addPixmap(pixmap,mode)
    return result
