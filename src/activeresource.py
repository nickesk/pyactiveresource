#!/usr/bin/python2.4
# Authors: Jared Kuolt <me@superjared.com>, Mark Roach <mrroach@google.com>

"""Connect to and interact with a REST server and its objects."""


import re
import urllib
import urlparse
from string import Template
from pyactiveresource import connection
from pyactiveresource import util

try:
    from xml.etree import cElementTree as ET
except ImportError:
    try:
        import cElementTree as ET
    except ImportError:
        try:
            from xml.etree import ElementTree as ET
        except ImportError:
            from elementtree import ElementTree as ET

VALID_NAME = re.compile('[a-z_]\w*')  # Valid python attribute names

# A global registry of known resource types
# TODO(mrroach). Track new object types in the metaclass, this is to
# provide a feature similar to the ruby implementation's find_or_create_*
_all_resources = {}


class Error(Exception):
    """A general error derived from Exception."""
    pass


class ResourceMeta(type):
    """A metaclass to handle singular/plural attributes."""

    def __new__(mcs, name, bases, new_attrs):
        """Create a new class.

        Args:
            mcs: The class to create.
            name: The name of the class.
            bases: List of base classes from which mcs inherits.
            new_attrs: The class attribute dictionary.
        """
        if '_singular' not in new_attrs or not new_attrs['_singular']:
            # Convert CamelCase to lower_underscore
            singular = re.sub(r'\B((?<=[a-z])[A-Z]|[A-Z](?=[a-z]))',
                              r'_\1', name).lower()
            new_attrs['_singular'] = singular

        if '_plural' not in new_attrs or not new_attrs['_plural']:
            new_attrs['_plural'] = util.pluralize(new_attrs['_singular'])

        return type.__new__(mcs, name, bases, new_attrs)


class ActiveResource(object):
    """Represents an activeresource object."""
    
    __metaclass__ = ResourceMeta

    _site = ''
    _user = ''
    _password = ''
    _connection_obj = None
    _headers = None
    _timeout = 5

    def __init__(self, attributes, **kwargs):
        """Initialize a new ActiveResource object.

        Args:
            attributes: A dictionary of attributes which represent this object.
        """
        self.attributes = {}
        self._prefix_options = kwargs.get('prefix_options', {})
        self._update(attributes)
        self._initialized = True

    # Public class methods which act as factory functions
    @classmethod
    def find(cls, id_=None, from_=None, **kwargs):
        """Core method for finding resources.

        Args:
            id_: A specific resource to retrieve.
            from_: The path that resources will be fetched from.
            kwargs: any keyword arguments for query.

        Returns:
            An ActiveResource object.
        Raises:
            connection.Error: On any communications errors.
            Error: On any other errors.
        """
        if id_:
            return cls._find_single(id_, **kwargs)

        return cls._find_every(from_=from_, **kwargs)

    @classmethod
    def find_one(cls, from_, **kwargs):
        """Get a single resource from a specific URL.

        Args:
            from_: The path that resources will be fetched from.
            kwargs: Any keyword arguments for query.
        Returns:
            An ActiveResource object.
        Raises:
            connection.Error: On any communications errors.
            Error: On any other errors.
        """
        return cls._find_one(from_, kwargs)

    @classmethod
    def exists(cls, id_, **kwargs):
        """Check whether a resource exists.
        
        Args:
            id_: The id or other key which specifies a unique object.
            kwargs: Any keyword arguments for query.
        Returns:
            True if the resource is found, False otherwise.
        """
        prefix_options, query_options = cls._split_options(kwargs)
        path = cls._element_path(id_, prefix_options, query_options)
        try:
            _ = cls._connection().head(path, cls._headers)
            return True
        except connection.Error:
            return False

    # Non-public class methods to support the above
    @classmethod
    def _split_options(cls, options):
        """Split prefix options and query options.

        Args:
            options: A dictionary of prefix and/or query options.
        Returns:
            A tuple containing (prefix_options, query_options)
        """
        #TODO(mrroach): figure out prefix_options
        prefix_options = {}
        query_options = {}
        for key, value in options.items():
            if key in cls._prefix_parameters():
                prefix_options[key] = value
            else:
                query_options[key] = value
        return [prefix_options, query_options]

    @classmethod
    def _find_single(cls, id_, **kwargs):
        """Get a single object from the default URL.

        Args:
            id_: The id or other key which specifies a unique object.
            kwargs: Any keyword arguments for the query.
        Returns:
            An ActiveResource object.
        Raises:
            ConnectionError: On any error condition.
        """
        prefix_options, query_options = cls._split_options(kwargs)
        path = cls._element_path(id_, prefix_options, query_options)
        return cls._build_object(cls._connection().get(path, cls._headers))


    @classmethod
    def _find_one(cls, from_, query_options):
        """Find a single resource from a one-off URL.

        Args:
            from_: The path from which to retrieve the resource.
            query_options: Any keyword arguments for the query.
        Returns:
            An ActiveResource object.
        Raises:
            connection.ConnectionError: On any error condition.
        """
        #TODO(mrroach): allow from_ to be a string-generating function
        path = from_ + cls._query_string(query_options)
        return cls._build_object(cls._connection().get(from_, cls._headers))

    @classmethod
    def _find_every(cls, from_=None, **kwargs):
        """Get all resources.
        
        Args:
            from_: (optional) The path from which to retrieve the resource.
            kwargs: Any keyword arguments for the query.
        Returns:
            A list of resources.
        """
        if from_:
            path = from_ + cls._query_string(kwargs)
            prefix_options = None
        else:
            prefix_options, query_options = cls._split_options(kwargs)
            path = cls._collection_path(prefix_options, query_options)
        return cls._build_list(cls._connection().get(path, cls._headers),
                               prefix_options)

    @classmethod
    def _build_object(cls, xml, prefix_options=None):
        """Create an object or objects for the given xml string.

        Args:
            xml: An xml string containing the object definition.
        Returns:
            An ActiveResource object.
        """
        if not prefix_options:
            prefix_options = {}
        attributes = util.xml_to_dict(xml)
        return cls(attributes, prefix_options=prefix_options)

    @classmethod
    def _build_list(cls, xml, prefix_options=None):
        """Create a list of objects for the given xml string.

        Args:
            xml: An xml string containing multiple object definitions.
        Returns:
            A list of ActiveResource objects.
        """
        elements = []
        root_element = ET.fromstring(xml)
        for element in root_element.getchildren():
            elements.append(cls._build_object(element, prefix_options))
        return elements
        
    @classmethod
    def _query_string(cls, query_options):
        """Return a query string for the given options.

        Args:
            query_options: A dictionary of query keys/values.
        Returns:
            A string containing the encoded query.
        """
        if query_options:
            return '?' + urllib.urlencode(query_options)
        else:
            return ''

    @classmethod
    def _element_path(cls, id_, prefix_options=None, query_options=None):
        """Get the element path for the given id.

        Examples:
            Comment.element_path(1, {'post_id': 5}) -> /posts/5/act
        Args:
            id_: The id of the object to retrieve.
            prefix_options: A dict of prefixes to add to the request for
                            nested URLs.
            query_options: A dict of items to add to the query string for
                           the request.
        Returns:
            The path (relative to site) to the element formatted with the query.
        """
        return '%(prefix)s/%(plural)s/%(id)s.%(format)s%(query)s' % {
                'prefix': cls._prefix(prefix_options),
                'plural': cls._plural,
                'id': id_,
                'format': 'xml',
                'query': cls._query_string(query_options)}

    @classmethod
    def _collection_path(cls, prefix_options=None, query_options=None):
        """Get the collection path for this object type.

        Examples:
            Comment.collection_path() -> /comments.xml
            Comment.collection_path(query_options={'active': 1})
                -> /comments.xml?active=1
            Comment.collection_path({'posts': 5})
                -> /posts/5/comments.xml
        Args:
            prefix_options: A dict of prefixes to add to the request for
                            nested URLs
            query_options: A dict of items to add to the query string for
                           the request.
        Returns:
            The path (relative to site) to this type of collection.
        """
        return '%(prefix)s/%(plural)s.%(format)s%(query)s' % {
                'prefix': cls._prefix(prefix_options),
                'plural': cls._plural,
                'format': 'xml',
                'query': cls._query_string(query_options)}

    @classmethod
    def _prefix_parameters(cls):
        """Return a list of the parameters used in the site prefix.
        
        e.g. /objects/$object_id would yield ['object_id']
             /objects/${object_id}/people/$person_id/ would yield
             ['object_id', 'person_id']
        Args:
            None
        Returns:
            A set of named parameters.
        """
        path = urlparse.urlsplit(cls._site)[2]        
        template = Template(path)
        keys = set()
        for match in template.pattern.finditer(path):
            for match_type in 'braced', 'named':
                if match.groupdict()[match_type]:
                    keys.add(match.groupdict()[match_type])
        return keys

    @classmethod
    def _prefix(cls, options=None):
        """Return the prefix for this object type.

        Args:
            options: A dictionary containing additional prefixes to prepend.
        Returns:
            A string containing the path to this element.
        """
        path = re.sub('/$', '', urlparse.urlsplit(cls._site)[2])
        template = Template(path)
        keys = cls._prefix_parameters()
        options = dict([(k, options.get(k, '')) for k in keys])
        return template.safe_substitute(options)

    @classmethod
    def _connection(cls):
        """Return a connection object which handles HTTP requests."""
        if not cls._connection_obj:
            cls._connection_obj = connection.Connection(
                    cls._site, cls._user, cls._password, cls._timeout)
        return cls._connection_obj

    @classmethod
    def _scrub_name(cls, name):
        """Remove invalid characters from attribute names.

        Args:
            name: the string to scrub
        Returns:
            The part of the string that is a valid name, or None if unscrubbable
        """
        name = name.lower().replace('-', '_')
        match = VALID_NAME.search(name)
        if match:
            return match.group(0)
        return None

    def to_dict(self):
        """Convert the object to a dictionary."""
        values = {}
        for key, value in self.attributes.iteritems():
            if isinstance(value, list):
                values[key] = [i.to_dict() for i in value]
            elif isinstance(value, ActiveResource):
                values[key] = value.to_dict()
            else:
                values[key] = value
        return values
                
    # Public instance methods
    def to_xml(self, root=None, header=True, pretty=False):
        """Convert the object to an xml string.

        Args:
            root: The name of the root element for xml output.
            header: Whether to include the xml header.
        Returns:
            An xml string.
        """
        if not root:
            root = self._singular
        return util.to_xml(self.to_dict(), root=root,
                                header=header, pretty=pretty)
    
    def save(self):
        """Save the object to the server.

        Args:
            None
        Returns:
            None.
        Raises:
            connection.Error: On any communications problems.
        """
        if self.id:
            response = self._connection().put(
                    self._element_path(self.id, self._prefix_options),
                    self._headers,
                    data=self.to_xml())
        else:
            response = self._connection().post(
                    self._collection_path(self._prefix_options),
                    self._headers,
                    data=self.to_xml())
        try:
            attributes = util.xml_to_dict(response)
        except Error:
            return
        self._update(attributes)
        return response

    def destroy(self):
        """Deletes the resource from the remote service.

        Args:
            None
        Returns:
            None
        """
        self._connection().delete(
                self._element_path(self.id, self._prefix_options),
                self.__class__._headers)

    def __getattr__(self, name):
        """Retrieve the requested attribute if it exists.

        Args:
            name: The attribute name.
        Returns:
            The attribute's value.
        Raises:
            AttributeError: if no such attribute exists.
        """
        #TODO(mrroach): Use descriptors instead of __getattr__
        if name == 'id':
            # id should always be getattrable
            return self.attributes.get('id')
        if name in self.attributes:
            return self.attributes[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        """Set the named attributes.

        Args:
            name: The attribute name.
            value: The attribute's value.
        Returns:
            None
        """
        if '_initialized' in self.__dict__:
            if name in self.__dict__:
                # Update a normal attribute
                self.__dict__[name] = value
            elif name in self.__class__.__dict__:
                # Update a class attribute
                self.__class__.__dict__[name] = value
            else:
                # Add/update an attribute
                self.attributes[name] = value
        self.__dict__[name] = value

    def __repr__(self):
        return '%s(%s)' % (self._singular, self.id)

    def __cmp__(self, other):
        if isinstance(other, self.__class__):
            return cmp(self.id, other.id)
        else:
            return cmp(self.id, other)

    def _update(self, attributes):
        """Update the object with the given attributes.

        Args:
            attributes: A dictionary of attributes.
        Returns:
            None
        """
        self.attributes = {}
        # Add all the tags in the element as attributes
        for key, value in attributes.items():
            if isinstance(value, dict):
                attr = self.__class__(value)
            elif isinstance(value, list):
                attr = [self.__class__(child) for child in value]
            else:
                attr = value
            # Store the actual value in the attributes dictionary
            self.attributes[key] = attr
            attr_name = self._scrub_name(key)
            if attr_name != key:
                # key is not a valid attribute name,
                # access via self.attributes[key]
                continue

