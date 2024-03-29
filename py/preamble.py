import inspect
import sys
import os
import sysconfig
import site


# Although upstream pybind11 allows user sites (check site.ENABLE_USER_SITE),
# older versions did not.
ver = sysconfig.get_python_version()
sys.path.append(os.path.expanduser(f"~/.local/lib/python{ver}/site-packages"))

# Similarly, the working directory is a reasonable expectation not met by some
# versions of pybind11.
sys.path.append(".")


def describe(registry, request):
    '''
    Tuber slow path
    
    This is invoked with a "request" object that does _not_ contain "object"
    and "method" keys, which would indicate a RPC operation.

    Instead, we are requesting one of the following:

    - An object descriptor ("object" but no "method" or "property")
    - A method descriptor ("object" and a "property" corresponding to a method)
    - A property descriptor ("object" and a "property" that is static data)

    Since these are all cached on the client side, we are more concerned about
    correctness and robustness than performance here.
    '''

    objname = request['object'] if 'object' in request else None
    methodname = request['method'] if 'method' in request else None
    propertyname = request['property'] if 'property' in request else None

    try:
       obj = registry[objname]
    except KeyError:
        return {
            'error': {
                'message': f"Request for an object ({objname}) that wasn't in the registry!"
            }
        }

    if not methodname and not propertyname:
        # Object metadata.
        methods = []
        properties = []

        for c in dir(obj):
            # Don't export dunder methods or attributes - this avoids exporting
            # Python internals on the server side to any client.
            if c.startswith('__'):
                continue

            if callable(getattr(obj, c)):
                methods.append(c)
            else:
                properties.append(c)

        return {
            'result': {
                '__doc__': inspect.getdoc(obj),
                'methods': methods,
                'properties': properties,
            }
        }

    if propertyname and hasattr(obj, propertyname):
        # Returning a method description or property evaluation
        attr = getattr(obj, propertyname)

        # Simple case: just a property evaluation
        if not callable(attr):
            return {'result': attr}

        # Complex case: return a description of a method
        return {
            'result': { '__doc__': inspect.getdoc(attr) }
        }
