/* ===== API Cache Module ===== */
(function() {
    var cache = new Map();
    var defaultTTL = 30000;
    var maxCacheSize = 50;

    function getCacheKey(url, options) {
        var key = url;
        if (options && options.body) {
            key += '_' + options.body;
        }
        return key;
    }

    function get(url, options, ttl) {
        var cacheKey = getCacheKey(url, options);
        var now = Date.now();
        var cached = cache.get(cacheKey);

        if (cached && now - cached.timestamp < (ttl || defaultTTL)) {
            return Promise.resolve(JSON.parse(JSON.stringify(cached.data)));
        }

        return fetch(url, options)
            .then(function(response) {
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                return response.json();
            })
            .then(function(data) {
                if (cache.size >= maxCacheSize) {
                    var oldestKey = cache.keys().next().value;
                    cache.delete(oldestKey);
                }
                cache.set(cacheKey, {
                    data: JSON.parse(JSON.stringify(data)),
                    timestamp: now
                });
                return data;
            });
    }

    function invalidate(pattern) {
        if (!pattern) {
            cache.clear();
            return;
        }
        var regex = new RegExp(pattern);
        var keysToDelete = [];
        cache.forEach(function(_, key) {
            if (regex.test(key)) {
                keysToDelete.push(key);
            }
        });
        keysToDelete.forEach(function(key) {
            cache.delete(key);
        });
    }

    function getStats() {
        return {
            size: cache.size,
            maxSize: maxCacheSize
        };
    }

    window.ApiCache = {
        get: get,
        invalidate: invalidate,
        getStats: getStats,
        setDefaultTTL: function(ttl) { defaultTTL = ttl; },
        setMaxSize: function(size) { maxCacheSize = size; }
    };
})();
