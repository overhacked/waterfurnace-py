import asyncio
from datetime import datetime, timedelta
import functools


__all__ = ['timed_cache']


# Adapted from:
# for asyncio:
#     https://gist.github.com/dlebech/c16a34f735c0c4e9b604
# for timed expiration:
#     https://gist.github.com/Morreski/c1d08a3afa4040815eafd3891e16b945


def _wrap_coroutine_storage(cache_dict, key, future):
    async def wrapper():
        val = await future
        cache_dict[key] = val
        return val
    return wrapper()


def _wrap_value_in_coroutine(val):
    async def wrapper():
        return val
    return wrapper()


def timed_cache(**timedelta_kwargs):

    __cache = dict()

    def _wrapper(f):
        update_delta = timedelta(**timedelta_kwargs)
        next_update = datetime.utcnow() + update_delta

        @functools.wraps(f)
        def _wrapped(*args, **kwargs):
            nonlocal next_update
            now = datetime.utcnow()
            if now >= next_update:
                __cache.clear()
                next_update = now + update_delta
            # Simple key generation. Notice that there are
            # no guarantees that the key will be the same
            # when using dict arguments.
            key = f.__module__ + '#' + f.__name__ + '#' + repr((args, kwargs))
            try:
                val = __cache[key]
                if asyncio.iscoroutinefunction(f):
                    return _wrap_value_in_coroutine(val)
                return val
            except KeyError:
                val = f(*args, **kwargs)

                if asyncio.iscoroutine(val):
                    # If the value returned by the function
                    # is a coroutine, wrap the future in a new coroutine
                    # that stores the actual result in the cache.
                    return _wrap_coroutine_storage(__cache, key, val)

                # Otherwise just store and return the value directly
                __cache[key] = val
                return val
            return f(*args, **kwargs)
        return _wrapped
    return _wrapper
