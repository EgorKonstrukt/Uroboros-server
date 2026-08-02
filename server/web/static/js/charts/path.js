window.Charts = window.Charts || {};

Charts.Path = {
    smooth: function (xy) {
        var d = 'M' + xy[0][0].toFixed(1) + ' ' + xy[0][1].toFixed(1);
        for (var j = 0; j < xy.length - 1; j++) {
            var p0 = xy[j - 1] || xy[j];
            var p1 = xy[j];
            var p2 = xy[j + 1];
            var p3 = xy[j + 2] || p2;
            var c1x = p1[0] + (p2[0] - p0[0]) / 6;
            var c1y = p1[1] + (p2[1] - p0[1]) / 6;
            var c2x = p2[0] - (p3[0] - p1[0]) / 6;
            var c2y = p2[1] - (p3[1] - p1[1]) / 6;
            d += 'C' + c1x.toFixed(1) + ' ' + c1y.toFixed(1) + ' ' +
                c2x.toFixed(1) + ' ' + c2y.toFixed(1) + ' ' + p2[0].toFixed(1) + ' ' + p2[1].toFixed(1);
        }
        return d;
    },

    stepped: function (xy) {
        var d = 'M' + xy[0][0].toFixed(1) + ' ' + xy[0][1].toFixed(1);
        for (var i = 1; i < xy.length; i++) {
            d += 'L' + xy[i][0].toFixed(1) + ' ' + xy[i - 1][1].toFixed(1) +
                'L' + xy[i][0].toFixed(1) + ' ' + xy[i][1].toFixed(1);
        }
        return d;
    },

    closed: function (path, firstX, lastX, baseY) {
        return path + 'L' + lastX.toFixed(1) + ' ' + baseY.toFixed(1) +
            'L' + firstX.toFixed(1) + ' ' + baseY.toFixed(1) + 'Z';
    }
};
