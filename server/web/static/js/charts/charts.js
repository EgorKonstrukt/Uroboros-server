window.Charts = window.Charts || {};

Charts.clamp = function (v, a, b) {
    return v < a ? a : (v > b ? b : v);
};

Charts.two = function (n) {
    return (n < 10 ? '0' : '') + n;
};

Charts.clock = function (ts) {
    var d = new Date(ts);
    return Charts.two(d.getHours()) + ':' + Charts.two(d.getMinutes()) + ':' + Charts.two(d.getSeconds());
};

Charts.cssVar = function (name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '';
};

Charts.hexA = function (hex, a) {
    var h = String(hex).trim();
    if (h.charAt(0) === '#') {
        var s = h.length === 4 ? h.slice(1).replace(/./g, function (c) { return c + c; }) : h.slice(1);
        var n = parseInt(s, 16);
        if (!isNaN(n)) {
            return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
        }
    }
    return h;
};

Charts.roundRectPath = function (ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
};

Charts.easeInOutCubic = function (t) {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
};

Charts.monoFont = function (weight, size) {
    return weight + ' ' + size + 'px ' + (Charts.cssVar('--mono') || 'Roboto Mono,monospace');
};

Charts.uiFont = function (weight, size) {
    return weight + ' ' + size + 'px ' + (Charts.cssVar('--font') || 'Roboto,sans-serif');
};
