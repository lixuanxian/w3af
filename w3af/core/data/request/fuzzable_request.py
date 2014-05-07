"""
FuzzableRequest.py

Copyright 2006 Andres Riancho

This file is part of w3af, http://w3af.org/ .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

"""
import copy
import string

from itertools import chain, izip_longest
from urllib import unquote

import w3af.core.controllers.output_manager as om

from w3af.core.data.constants.encodings import DEFAULT_ENCODING
from w3af.core.controllers.exceptions import BaseFrameworkException
from w3af.core.data.dc.cookie import Cookie
from w3af.core.data.dc.headers import Headers
from w3af.core.data.dc.data_container import DataContainer
from w3af.core.data.db.disk_item import DiskItem
from w3af.core.data.parsers.url import URL
from w3af.core.data.request.request_mixin import RequestMixIn


ALL_CHARS = ''.join(chr(i) for i in xrange(256))
TRANS_TABLE = string.maketrans(ALL_CHARS, ALL_CHARS)


class FuzzableRequest(RequestMixIn, DiskItem):
    """
    This class represents a fuzzable request. Fuzzable requests were created
    to allow w3af plugins to be much simpler and don't really care if the
    vulnerability is in the postdata, querystring, header, cookie or any other
    variable.

    Other classes should inherit from this one and change the behaviour of
    get_uri() and get_data(). For example: the class HTTPQSRequest should return
    the _dc in the querystring (get_uri) and HTTPPostDataRequest should return
    the _dc in the POSTDATA (get_data()).

    :author: Andres Riancho (andres.riancho@gmail.com)
    """

    def __init__(self, uri, method='GET', headers=None, cookie=None, dc=None):
        super(FuzzableRequest, self).__init__()
        
        # Internal variables
        self._dc = dc or DataContainer()
        self._method = method
        self._headers = Headers(headers or ())
        self._cookie = cookie or Cookie()
        self._data = None
        self.set_uri(uri)

        # Set the internal variables
        self._sent_info_comp = None

    def export(self):
        """
        Generic version of how they are exported:
            METHOD,URL,DC

        Example:
            GET,http://localhost/index.php?abc=123&def=789,
            POST,http://localhost/index.php,abc=123&def=789

        :return: a csv str representation of the request
        """
        #
        # FIXME: What if a comma is inside the URL or DC?
        # TODO: Why don't we export headers and cookies?
        #
        meth = self._method
        str_res = [meth, ',', str(self._url)]

        if meth == 'GET':
            if self._dc:
                str_res.extend(('?', str(self._dc)))
            str_res.append(',')
        else:
            str_res.append(',')
            if self._dc:
                str_res.append(str(self._dc))

        return ''.join(str_res)

    def sent(self, smth_instng):
        """
        Checks if something similar to `smth_instng` was sent in the request.
        This is used to remove false positives, e.g. if a grep plugin finds a
        "strange" string and wants to be sure it was not generated by an audit
        plugin.

        This method should only be used by grep plugins which often have false
        positives.

        The following example shows that we sent d'z"0 but d\'z"0 will
        as well be recognised as sent

        TODO: This function is called MANY times, and under some circumstances
        it's performance REALLY matters. We need to review this function.

        >>> f = FuzzableRequest(URL('''http://example.com/a?p=d'z"0&paged=2'''))
        >>> f.sent('d%5C%27z%5C%220')
        True

        >>> f._data = 'p=<SCrIPT>alert("bsMs")</SCrIPT>'
        >>> f.sent('<SCrIPT>alert(\"bsMs\")</SCrIPT>')
        True

        >>> f = FuzzableRequest(URL('http://example.com/?p=<ScRIPT>a=/PlaO/%0Afake_alert(a.source)</SCRiPT>'))
        >>> f.sent('<ScRIPT>a=/PlaO/fake_alert(a.source)</SCRiPT>')
        True

        :param smth_instng: The string
        :return: True if something similar was sent
        """
        def make_comp(heterogen_string):
            """
            This basically removes characters that are hard to compare
            """
            delete_chars = ('\\', '\'', '"', '+', ' ', chr(0),
                            chr(int("0D", 16)), chr(int("0A", 16)))
            return string.translate(heterogen_string, TRANS_TABLE, delete_chars)

        data = self._data or ''
        # This is the easy part. If it was exactly like this in the request
        if data and smth_instng in data or \
        smth_instng in self.get_uri() or \
        smth_instng in unquote(data) or \
        smth_instng in unicode(self._uri.url_decode()):
            return True

        # Ok, it's not in it but maybe something similar
        # Let's set up something we can compare
        if self._sent_info_comp is None:
            dc = self._dc
            dec_dc = unquote(str(dc)).decode(dc.encoding)
            data = '%s%s%s' % (unicode(self._uri), data, dec_dc)

            self._sent_info_comp = make_comp(data + unquote(data))

        min_len = 3
        # make the smth_instng comparable
        smth_instng_comps = (make_comp(smth_instng),
                             make_comp(unquote(smth_instng)))
        for smth_intstng_comp in smth_instng_comps:
            # We don't want false negatives just because the string is
            # short after making comparable
            if smth_intstng_comp in self._sent_info_comp and \
            len(smth_intstng_comp) >= min_len:
                return True

        # I didn't sent the smth_instng in any way
        return False

    def __hash__(self):
        return hash(str(self._uri))

    def __str__(self):
        """
        :return: A string representation of this fuzzable request.

        >>> fr = FuzzableRequest(URL("http://www.w3af.com/"))
        >>> str(fr)
        'http://www.w3af.com/ | Method: GET'

        >>> repr( fr )
        '<fuzzable request | GET | http://www.w3af.com/>'

        >>> fr.set_method('TRACE')
        >>> str(fr)
        'http://www.w3af.com/ | Method: TRACE'

        """
        strelems = [unicode(self._url)]
        strelems.append(u' | Method: ' + self._method)

        if self._dc:
            strelems.append(u' | Parameters: (')

            # Mangle the value for printing
            for pname, values in self._dc.items():
                # Because of repeated parameter names, we need to add this:
                for the_value in values:
                    # the_value is always a string
                    if len(the_value) > 10:
                        the_value = the_value[:10] + '...'
                    the_value = '"' + the_value + '"'
                    strelems.append(pname + '=' + the_value + ', ')

            strelems[-1] = strelems[-1][:-2]
            strelems.append(u')')

        return u''.join(strelems).encode(DEFAULT_ENCODING)

    def __repr__(self):
        return '<fuzzable request | %s | %s>' % \
            (self.get_method(), self.get_uri())

    def __eq__(self, other):
        """
        Two requests are equal if:
            - They have the same URL
            - They have the same method
            - They have the same parameters
            - The values for each parameter is equal

        :return: True if the requests are equal.
        """
        if isinstance(other, FuzzableRequest):
            return (self._method == other._method and
                    self._uri == other._uri and
                    self._dc == other._dc)
        else:
            return NotImplemented

    def get_eq_attrs(self):
        return ['_method', '_uri', '_dc']

    def __ne__(self, other):
        return not self.__eq__(other)

    def is_variant_of(self, other):
        """
        Two requests are loosely equal (or variants) if:
            - They have the same URL
            - They have the same HTTP method
            - They have the same parameter names
            - The values for each parameter have the same type (int / string)

        :return: True if self and other are variants.
        """
        dc = self._dc
        odc = other._dc

        if (self._method == other._method and
            self._url == other._url and
                dc.keys() == odc.keys()):
            for vself, vother in izip_longest(
                chain(*dc.values()),
                chain(*odc.values()),
                fillvalue=None
            ):
                if None in (vself, vother) or \
                        vself.isdigit() != vother.isdigit():
                    return False
            return True
        return False

    def set_url(self, url):
        if not isinstance(url, URL):
            raise TypeError('The "url" parameter of a %s must be of '
                            'url.URL type.' % type(self).__name__)

        self._url = URL(url.url_string.replace(' ', '%20'))
        self._uri = self._url

    def set_uri(self, uri):
        if not isinstance(uri, URL):
            raise TypeError('The "uri" parameter of a %s must be of '
                            'url.URL type.' % type(self).__name__)
        self._uri = uri
        self._url = uri.uri2url()

    def set_method(self, method):
        self._method = method

    def set_dc(self, dataCont):
        if not isinstance(dataCont, DataContainer):
            raise TypeError('Invalid call to fuzzable_request.set_dc(), the '
                            'argument must be a DataContainer instance.')
        self._dc = dataCont

    def set_headers(self, headers):
        self._headers = Headers(headers)

    def set_referer(self, referer):
        self._headers['Referer'] = str(referer)

    def set_cookie(self, c):
        """
        :param cookie: A Cookie object as defined in core.data.dc.cookie,
            or a string.
        """
        if isinstance(c, Cookie):
            self._cookie = c
        elif isinstance(c, basestring):
            self._cookie = Cookie(c)
        elif c is None:
            self._cookie = Cookie()
        else:
            fmt = '[FuzzableRequest error] set_cookie received: "%s": "%s".'
            error_str = fmt % (type(c), repr(c))
            om.out.error(error_str)
            raise BaseFrameworkException(error_str)

    def get_url(self):
        return self._url

    def get_uri(self):
        return self._uri

    def set_data(self, d):
        """
        The data is the string representation of the DataContainer, in most
        cases it wont be set.
        """
        self._data = d

    def get_data(self):
        """
        The data is the string representation of the DataContainer, in most
        cases it will be used as the POSTDATA for requests. Sometimes it is
        also used as the query string data.
        """
        return self._data

    def get_method(self):
        return self._method

    def get_dc(self):
        return self._dc

    def get_headers(self):
        return self._headers

    def get_referer(self):
        return self._headers.get('Referer', None)

    def get_cookie(self):
        return self._cookie

    def get_file_vars(self):
        return []

    def copy(self):
        return copy.deepcopy(self)
