# -*- encoding: UTF-8 -*-
import sublime
import sublime_plugin

import os
import sys
import traceback
import tempfile
import re
import json
import time
import codecs
import cgi
import yaml
# import logging as log
# from logging import (debug as print, error as eprint, info as iprint )
# log.BASIC_FORMAT(level=logging.DEBUG, format='%(levelname)s - %(message)s')

pygments_local = {
    'github': 'pygments_css/github.css',
    'github2014': 'pygments_css/github2014.css'
}


def is_ST3():
    ''' check if ST3 based on python version '''
    return sys.version_info >= (3, 0)

# Global var
refFile = ""


if is_ST3():
    from .helper import INSTALLED_DIRECTORY
    from . import desktop
    from .markdown_settings import Settings
    from .markdown_wrapper import StMarkdown as Markdown
    from urllib.request import urlopen, url2pathname, pathname2url
    from urllib.parse import urlparse, urlunparse
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from .markdown.extensions import codehilite

    def Request(url, data, headers):
        ''' Adapter for urllib2 used in ST2 '''
        import urllib.request
        return urllib.request.Request(url, data=data, headers=headers, method='POST')

    unicode_str = str

else:
    from helper import INSTALLED_DIRECTORY
    import desktop
    from markdown_settings import Settings
    from markdown_wrapper import StMarkdown as Markdown
    from urllib2 import Request, urlopen, HTTPError, URLError
    from urllib import quote, url2pathname, pathname2url
    from urlparse import urlparse, urlunparse
    import markdown.extensions.codehilite as codehilite

    unicode_str = unicode

from pygments.formatters import get_formatter_by_name
try:
    PYGMENTS_AVAILABLE = codehilite.pygments
except:
    PYGMENTS_AVAILABLE = False

_CANNOT_CONVERT = u'cannot convert markdown'

PATH_EXCLUDE = tuple(
    [
        'file://', 'https://', 'http://', '/', '#',
        "data:image/jpeg;base64,", "data:image/png;base64,", "data:image/gif;base64,"
    ] + ['\\'] if sys.platform.startswith('win') else []
)

ABS_EXCLUDE = tuple(
    [
        'file://', '/'
    ] + (['\\'] if sys.platform.startswith('win') else [])
)

DEFAULT_EXT = [
    "extra", "github", "toc",
    "meta", "sane_lists", "smarty", "wikilinks",
    "admonition"
]

__FILE__ = os.path.basename(__file__)

def getTempMarkdownPreviewPath(view):
    ''' return a permanent full path of the temp markdown preview file '''

    settings = sublime.load_settings('MarkdownPreview.sublime-settings')

    tmp_filename = '%s.html' % view.id()
    tmp_dir = tempfile.gettempdir()
    if settings.get('path_tempfile'):
        if os.path.isabs(settings.get('path_tempfile')):  # absolute path or not
            tmp_dir = settings.get('path_tempfile')
        else:
            tmp_dir = os.path.join(os.path.dirname(view.file_name()), settings.get('path_tempfile'))

    if not os.path.isdir(tmp_dir):  # create dir if not exsits
        os.makedirs(tmp_dir)

    tmp_fullpath = os.path.join(tmp_dir, tmp_filename)
    return tmp_fullpath


def save_utf8(filename, text):
    with codecs.open(filename, 'w', encoding='utf-8')as f:
        f.write(text)


def load_utf8(filename):
    with codecs.open(filename, 'r', encoding='utf-8') as f:
        return f.read()


def load_resource(name):
    ''' return file contents for files within the package root folder '''

    try:
        if is_ST3():
            return sublime.load_resource('Packages/Markdown Preview/{0}'.format(name))
        else:
            filename = os.path.join(sublime.packages_path(), INSTALLED_DIRECTORY, os.path.normpath(name))
            return load_utf8(filename)
    except:
        print("Error while load_resource('%s')" % name)
        traceback.print_exc()
        return ''


def exists_resource(resource_file_path):
    filename = os.path.join(os.path.dirname(sublime.packages_path()), resource_file_path)
    return os.path.isfile(filename)


def new_view(window, text, scratch=False):
    ''' create a new view and paste text content
        return the new view.
        Optionally can be set as scratch.
    '''

    new_view = window.new_file()
    if scratch:
        new_view.set_scratch(True)
    if is_ST3():
        new_view.run_command('append', {
            'characters': text,
        })
    else:  # 2.x
        new_edit = new_view.begin_edit()
        new_view.insert(new_edit, 0, text)
        new_view.end_edit(new_edit)
    return new_view


def get_references(file_name, encoding="utf-8"):
    """ Get footnote and general references from outside source """
    text = ''
    if file_name is not None:
        if os.path.exists(file_name):
            try:
                with codecs.open(file_name, "r", encoding=encoding) as f:
                    text = f.read()
            except:
                print(traceback.format_exc())
        else:
            print("Could not find reference file %s!", file_name)
    return text


def parse_url(url):
    """
    Parse the url and
    try to determine if the following is a file path or
    (as we will call anything else) a url
    """

    RE_PATH = re.compile(r'file|[A-Za-z]')
    RE_WIN_DRIVE = re.compile(r"[A-Za-z]:?")
    RE_URL = re.compile('(http|ftp)s?|data|mailto|tel|news')
    is_url = False
    is_absolute = False
    scheme, netloc, path, params, query, fragment = urlparse(url)

    if RE_URL.match(scheme):
        # Clearly a url
        is_url = True
    elif scheme == '' and netloc == '' and path == '':
        # Maybe just a url fragment
        is_url = True
    elif scheme == '' or RE_PATH.match(scheme):
        if sublime.platform() == "windows":
            if scheme == 'file' and RE_WIN_DRIVE.match(netloc):
                # file://c:/path
                path = netloc + path
                netloc = ''
                scheme = ''
                is_absolute = True
            elif RE_WIN_DRIVE.match(scheme):
                # c:/path
                path = '%s:%s' % (scheme, path)
                scheme = ''
                is_absolute = True
            elif scheme != '' or netloc != '':
                # Unknown url scheme
                is_url = True
            elif path.startswith('//'):
                # //Some/Network/location
                is_absolute = True
        else:
            if scheme not in ('', 'file') and netloc != '':
                # A non-nix filepath or strange url
                is_url = True
            else:
                # Check if nix path is absolute or not
                if path.startswith('/'):
                    is_absolute = True
                scheme = ''
    return (scheme, netloc, path, params, query, fragment, is_url, is_absolute)


def repl_relative(m, base_path, relative_path):
    """ Replace path with relative path """

    RE_WIN_DRIVE_PATH = re.compile(r"(^(?P<drive>[A-Za-z]{1}):(?:\\|/))")
    link = m.group(0)
    try:
        scheme, netloc, path, params, query, fragment, is_url, is_absolute = parse_url(m.group('path')[1:-1])

        if not is_url:
            # Get the absolute path of the file or return
            # if we can't resolve the path
            path = url2pathname(path)
            abs_path = None
            if (not is_absolute):
                # Convert current relative path to absolute
                temp = os.path.normpath(os.path.join(base_path, path))
                if os.path.exists(temp):
                    abs_path = temp.replace("\\", "/")
            elif os.path.exists(path):
                abs_path = path

            if abs_path is not None:
                convert = False
                # Determine if we should convert the relative path
                # (or see if we can realistically convert the path)
                if (sublime.platform() == "windows"):
                    # Make sure basepath starts with same drive location as target
                    # If they don't match, we will stay with absolute path.
                    if (base_path.startswith('//') and base_path.startswith('//')):
                        convert = True
                    else:
                        base_drive = RE_WIN_DRIVE_PATH.match(base_path)
                        path_drive = RE_WIN_DRIVE_PATH.match(abs_path)
                        if (
                            (base_drive and path_drive) and
                            base_drive.group('drive').lower() == path_drive.group('drive').lower()
                        ):
                            convert = True
                else:
                    # OSX and Linux
                    convert = True

                # Convert the path, url encode it, and format it as a link
                if convert:
                    path = pathname2url(os.path.relpath(abs_path, relative_path).replace('\\', '/'))
                else:
                    path = pathname2url(abs_path)
                link = '%s"%s"' % (m.group('name'), urlunparse((scheme, netloc, path, params, query, fragment)))
    except:
        # Parsing crashed an burned; no need to continue.
        pass

    return link


def repl_absolute(m, base_path):
    """ Replace path with absolute path """
    link = m.group(0)

    try:
        scheme, netloc, path, params, query, fragment, is_url, is_absolute = parse_url(m.group('path')[1:-1])

        if (not is_absolute and not is_url):
            path = url2pathname(path)
            temp = os.path.normpath(os.path.join(base_path, path))
            if os.path.exists(temp):
                path = pathname2url(temp.replace("\\", "/"))
                link = '%s"%s"' % (m.group('name'), urlunparse((scheme, netloc, path, params, query, fragment)))
    except Exception:
        # Parsing crashed an burned; no need to continue.
        pass

    return link


class CriticDump(object):
    RE_CRITIC = re.compile(
        r'''
            ((?P<open>\{)
                (?:
                    (?P<ins_open>\+{2})(?P<ins_text>.*?)(?P<ins_close>\+{2})
                  | (?P<del_open>\-{2})(?P<del_text>.*?)(?P<del_close>\-{2})
                  | (?P<mark_open>\={2})(?P<mark_text>.*?)(?P<mark_close>\={2})
                  | (?P<comment>(?P<com_open>\>{2})(?P<com_text>.*?)(?P<com_close>\<{2}))
                  | (?P<sub_open>\~{2})(?P<sub_del_text>.*?)(?P<sub_mid>\~\>)(?P<sub_ins_text>.*?)(?P<sub_close>\~{2})
                )
            (?P<close>\})|.)
        ''',
        re.MULTILINE | re.DOTALL | re.VERBOSE
    )

    def process(self, m):
        if self.accept:
            if m.group('ins_open'):
                return m.group('ins_text')
            elif m.group('del_open'):
                return ''
            elif m.group('mark_open'):
                return m.group('mark_text')
            elif m.group('com_open'):
                return ''
            elif m.group('sub_open'):
                return m.group('sub_ins_text')
            else:
                return m.group(0)
        else:
            if m.group('ins_open'):
                return ''
            elif m.group('del_open'):
                return m.group('del_text')
            elif m.group('mark_open'):
                return m.group('mark_text')
            elif m.group('com_open'):
                return ''
            elif m.group('sub_open'):
                return m.group('sub_del_text')
            else:
                return m.group(0)

    def dump(self, source, accept):
        text = ''
        self.accept = accept
        for m in self.RE_CRITIC.finditer(source):
            text += self.process(m)
        return text


class MarkdownPreviewListener(sublime_plugin.EventListener):
    ''' auto update the output html if markdown file has already been converted once '''

    def on_post_save(self, view):
        settings = sublime.load_settings('MarkdownPreview.sublime-settings')
        if settings.get('enable_autoreload', True):
            filetypes = settings.get('markdown_filetypes')
            file_name = view.file_name()
            if filetypes and file_name is not None and file_name.endswith(tuple(filetypes)):
                temp_file = getTempMarkdownPreviewPath(view)
                if os.path.isfile(temp_file):
                    # reexec markdown conversion
                    # todo : check if browser still opened and reopen it if needed
                    view.run_command('markdown_preview', {
                        'target': 'disk',
                        'parser': view.settings().get('parser')
                    })
                    sublime.status_message('Markdown preview file updated')


class MarkdownCheatsheetCommand(sublime_plugin.TextCommand):
    ''' open our markdown cheat sheet in ST2 '''
    def run(self, edit):
        lines = '\n'.join(load_resource('sample.md').splitlines())
        view = new_view(self.view.window(), lines, scratch=True)
        view.set_name("Markdown Cheatsheet")

        # Set syntax file
        syntax_files = ["Packages/Markdown Extended/Syntaxes/Markdown Extended.tmLanguage", "Packages/Markdown/Markdown.tmLanguage"]
        for file in syntax_files:
            if exists_resource(file):
                view.set_syntax_file(file)
                break  # Done if any syntax is set.

        sublime.status_message('Markdown cheat sheet opened')


class Compiler(object):
    ''' Do the markdown converting '''
    default_css = "markdown.css"

    def isurl(self, css_name):
        match = re.match(r'https?://', css_name)
        if match:
            return True
        return False

    def get_default_css(self):
        ''' locate the correct CSS with the 'css' setting '''
        css_list = self.settings.get('css', ['default'])

        if not isinstance(css_list, list):
            css_list = [css_list]

        css_text = []
        for css_name in css_list:
            if self.isurl(css_name):
                # link to remote URL
                css_text.append(u"<link href='%s' rel='stylesheet' type='text/css'>" % css_name)
            elif os.path.isfile(os.path.expanduser(css_name)):
                # use custom CSS file
                css_text.append(u"<style>%s</style>" % load_utf8(os.path.expanduser(css_name)))
            elif css_name == 'default':
                # use parser CSS file
                css_text.append(u"<style>%s</style>" % load_resource(self.default_css))

        return u'\n'.join(css_text)

    def get_override_css(self):
        ''' handls allow_css_overrides setting. '''

        if self.settings.get('allow_css_overrides'):
            filename = self.view.file_name()
            filetypes = self.settings.get('markdown_filetypes')

            if filename and filetypes:
                for filetype in filetypes:
                    if filename.endswith(filetype):
                        css_filename = filename.rpartition(filetype)[0] + '.css'
                        if (os.path.isfile(css_filename)):
                            return u"<style>%s</style>" % load_utf8(css_filename)
        return ''

    def get_stylesheet(self):
        ''' return the correct CSS file based on parser and settings '''
        return self.get_default_css() + self.get_override_css()

    def get_javascript(self):
        js_files = self.settings.get('js')
        scripts = ''

        if js_files is not None:
            # Ensure string values become a list.
            if isinstance(js_files, str) or isinstance(js_files, unicode_str):
                js_files = [js_files]
            # Only load scripts if we have a list.
            if isinstance(js_files, list):
                for js_file in js_files:
                    if os.path.isabs(js_file):
                        # Load the script inline to avoid cross-origin.
                        scripts += u"<script>%s</script>" % load_utf8(js_file)
                    else:
                        scripts += u"<script type='text/javascript' src='%s'></script>" % js_file
        return scripts

    def get_mathjax(self):
        ''' return the MathJax script if enabled '''

        if self.settings.get('enable_mathjax') is True:
            return load_resource('mathjax.html')
        return ''

    def get_uml(self):
        ''' return the uml scripts if enabled '''

        if self.settings.get('enable_uml') is True:
            flow = load_resource('flowchart-min.js')
            return load_resource('uml.html').replace('{{ flowchart }}', flow, 1)
        return ''

    def get_highlight(self):
        return ''

    def get_contents(self, wholefile=False):
        ''' Get contents or selection from view and optionally strip the YAML front matter '''
        region = sublime.Region(0, self.view.size())
        contents = self.view.substr(region)
        if not wholefile:
            # use selection if any
            selection = self.view.substr(self.view.sel()[0])
            if selection.strip() != '':
                contents = selection

        # Remove yaml front matter
        if self.settings.get('strip_yaml_front_matter') and contents.startswith('---'):
            frontmatter, contents = self.preprocessor_yaml_frontmatter(contents)
            self.settings.apply_frontmatter(frontmatter)

        references = self.settings.get('builtin').get('references', [])
        for ref in references:
            contents += get_references(ref)

        contents = self.parser_specific_preprocess(contents)

        return contents

    def parser_specific_preprocess(self, text):
        return text

    def preprocessor_yaml_frontmatter(self, text):
        """ Get frontmatter from string """
        frontmatter = {}

        if text.startswith("---"):
            m = re.search(r'^(---(.*?)---[ \t]*\r?\n)', text, re.DOTALL)
            if m:
                try:
                    frontmatter = yaml.load(m.group(2))
                except:
                    print(traceback.format_exc())
                text = text[m.end(1):]

        return frontmatter, text

    def parser_specific_postprocess(self, text):
        return text

    def postprocessor_pathconverter(self, html, image_convert, file_convert, absolute=False):

        RE_TAG_HTML = r'''(?xus)
        (?:
            (?P<comments>(\r?\n?\s*)<!--[\s\S]*?-->(\s*)(?=\r?\n)|<!--[\s\S]*?-->)|
            (?P<open><(?P<tag>(?:%s)))
            (?P<attr>(?:\s+[\w\-:]+(?:\s*=\s*(?:"[^"]*"|'[^']*'))?)*)
            (?P<close>\s*(?:\/?)>)
        )
        '''

        RE_TAG_LINK_ATTR = re.compile(
            r'''(?xus)
            (?P<attr>
                (?:
                    (?P<name>\s+(?:href|src)\s*=\s*)
                    (?P<path>"[^"]*"|'[^']*')
                )
            )
            '''
        )

        RE_SOURCES = re.compile(
            RE_TAG_HTML % (
                (r"img" if image_convert else "") +
                (r"|" if image_convert and file_convert else "") +
                (r"script|a|link" if file_convert else "")
            )
        )

        def repl(m, base_path, rel_path=None):
            if m.group('comments'):
                tag = m.group('comments')
            else:
                tag = m.group('open')
                if rel_path is None:
                    tag += RE_TAG_LINK_ATTR.sub(lambda m2: repl_absolute(m2, base_path), m.group('attr'))
                else:
                    tag += RE_TAG_LINK_ATTR.sub(lambda m2: repl_relative(m2, base_path, rel_path), m.group('attr'))
                tag += m.group('close')
            return tag

        basepath = self.settings.get('builtin').get("basepath")
        if basepath is None:
            basepath = ""

        if absolute:
            if basepath:
                return RE_SOURCES.sub(lambda m: repl(m, basepath), html)
        else:
            if self.preview:
                relativepath = getTempMarkdownPreviewPath(self.view)
            else:
                relativepath = self.settings.get('builtin').get("destination")
                if not relativepath:
                    mdfile = self.view.file_name()
                    if mdfile is not None and os.path.exists(mdfile):
                        relativepath = os.path.splitext(mdfile)[0] + '.html'

            if relativepath:
                relativepath = os.path.dirname(relativepath)

            if basepath and relativepath:
                return RE_SOURCES.sub(lambda m: repl(m, basepath, relativepath), html)
        return html

    def postprocessor_base64(self, html):
        ''' convert resources (currently images only) to base64 '''

        file_types = {
            (".png",): "image/png",
            (".jpg", ".jpeg"): "image/jpeg",
            (".gif",): "image/gif"
        }

        exclusion_list = tuple(
            ['https://', 'http://', '#'] +
            ["data:%s;base64," % ft for ft in file_types.values()]
        )

        RE_WIN_DRIVE = re.compile(r"(^[A-Za-z]{1}:(?:\\|/))")
        RE_TAG_HTML = re.compile(
            r'''(?xus)
            (?:
                (?P<comments>(\r?\n?\s*)<!--[\s\S]*?-->(\s*)(?=\r?\n)|<!--[\s\S]*?-->)|
                (?P<open><(?P<tag>img))
                (?P<attr>(?:\s+[\w\-:]+(?:\s*=\s*(?:"[^"]*"|'[^']*'))?)*)
                (?P<close>\s*(?:\/?)>)
            )
            '''
        )
        RE_TAG_LINK_ATTR = re.compile(
            r'''(?xus)
            (?P<attr>
                (?:
                    (?P<name>\s+src\s*=\s*)
                    (?P<path>"[^"]*"|'[^']*')
                )
            )
            '''
        )

        def b64(m):
            import base64
            data = m.group(0)
            try:
                src = url2pathname(m.group('path')[1:-1])
                base_path = self.settings.get('builtin').get("basepath")
                if base_path is None:
                    base_path = ""

                # Format the link
                absolute = False
                if src.startswith('file://'):
                    src = src.replace('file://', '', 1)
                    if sublime.platform() == "windows" and not src.startswith('//'):
                        src = src.lstrip("/")
                    absolute = True
                elif sublime.platform() == "windows" and RE_WIN_DRIVE.match(src) is not None:
                    absolute = True

                # Make sure we are working with an absolute path
                if not src.startswith(exclusion_list):
                    if absolute:
                        src = os.path.normpath(src)
                    else:
                        src = os.path.normpath(os.path.join(base_path, src))

                    if os.path.exists(src):
                        ext = os.path.splitext(src)[1].lower()
                        for b64_ext in file_types:
                            if ext in b64_ext:
                                with open(src, "rb") as f:
                                    data = " src=\"data:%s;base64,%s\"" % (
                                        file_types[b64_ext],
                                        base64.b64encode(f.read()).decode('ascii')
                                    )
                                break
            except Exception:
                pass
            return data

        def repl(m):
            if m.group('comments'):
                tag = m.group('comments')
            else:
                tag = m.group('open')
                tag += RE_TAG_LINK_ATTR.sub(lambda m2: b64(m2), m.group('attr'))
                tag += m.group('close')
            return tag

        return RE_TAG_HTML.sub(repl, html)

    def postprocessor_simple(self, html):
        ''' Strip out ids and classes for a simplified HTML output '''

        def repl(m):
            if m.group('comments'):
                tag = ''
            else:
                tag = m.group('open')
                tag += RE_TAG_BAD_ATTR.sub('', m.group('attr'))
                tag += m.group('close')
            return tag

        # Strip out id, class, on<word>, and style attributes for a simple html output
        RE_TAG_HTML = re.compile(
            r'''(?x)
            (?:
                (?P<comments>(\r?\n?\s*)<!--[\s\S]*?-->(\s*)(?=\r?\n)|<!--[\s\S]*?-->)|
                (?P<open><[\w\:\.\-]+)
                (?P<attr>(?:\s+[\w\-:]+(?:\s*=\s*(?:"[^"]*"|'[^']*'))?)*)
                (?P<close>\s*(?:\/?)>)
            )
            ''',
            re.DOTALL | re.UNICODE
        )

        RE_TAG_BAD_ATTR = re.compile(
            r'''(?x)
            (?P<attr>
                (?:
                    \s+(?:id|class|style|on[\w]+)
                    (?:\s*=\s*(?:"[^"]*"|'[^']*'))
                )*
            )
            ''',
            re.DOTALL | re.UNICODE
        )

        return RE_TAG_HTML.sub(repl, html)

    def convert_markdown(self, markdown_text):
        ''' convert input markdown to HTML, with github or builtin parser '''

        markdown_html = self.parser_specific_convert(markdown_text)

        image_convert = self.settings.get("image_path_conversion", "absolute")
        file_convert = self.settings.get("file_path_conversions", "absolute")

        markdown_html = self.parser_specific_postprocess(markdown_html)

        if "absolute" in (image_convert, file_convert):
            markdown_html = self.postprocessor_pathconverter(markdown_html, image_convert, file_convert, True)

        if "relative" in (image_convert, file_convert):
            markdown_html = self.postprocessor_pathconverter(markdown_html, image_convert, file_convert, False)

        if image_convert == "base64":
            markdown_html = self.postprocessor_base64(markdown_html)

        if self.settings.get("html_simple", False):
            markdown_html = self.postprocessor_simple(markdown_html)

        return markdown_html

    def get_title(self):
        if self.meta_title is not None:
            title = self.meta_title
        else:
            title = self.view.name()
        if not title:
            fn = self.view.file_name()
            title = 'untitled' if not fn else os.path.splitext(os.path.basename(fn))[0]
        return '<title>%s</title>' % cgi.escape(title)

    def get_meta(self):
        self.meta_title = None
        meta = []
        for k, v in self.settings.get("meta", {}).items():
            if k == "title":
                if isinstance(v, list):
                    if len(v) == 0:
                        v = ""
                    else:
                        v = v[0]
                self.meta_title = unicode_str(v)
                continue
            if isinstance(v, list):
                v = ','.join(v)
            if v is not None:
                meta.append(
                    '<meta name="%s" content="%s">' % (cgi.escape(k, True), cgi.escape(v, True))
                )
        return '\n'.join(meta)

    def run(self, view, wholefile=False, preview=False):
        ''' return full html and body html for view. '''
        self.settings = Settings('MarkdownPreview.sublime-settings', view.file_name())
        self.preview = preview
        self.view = view

        contents = self.get_contents(wholefile)

        body = self.convert_markdown(contents)

        html_template = self.settings.get('html_template')

        # use customized html template if given
        if self.settings.get('html_simple', False):
            html = body
        elif html_template and os.path.exists(html_template):
            head = u''
            head += self.get_meta()
            if not self.settings.get('skip_default_stylesheet'):
                head += self.get_stylesheet()
            head += self.get_javascript()
            head += self.get_highlight()
            head += self.get_mathjax()
            head += self.get_uml()
            head += self.get_title()

            html = load_utf8(html_template)
            html = html.replace('{{ HEAD }}', head, 1)
            html = html.replace('{{ BODY }}', body, 1)
        else:
            html = u'<!DOCTYPE html>'
            html += '<html><head><meta charset="utf-8">'
            html += self.get_meta()
            html += self.get_stylesheet()
            html += self.get_javascript()
            html += self.get_highlight()
            html += self.get_mathjax()
            html += self.get_uml()
            html += self.get_title()
            html += '</head><body>'
            html += '<article class="markdown-body">'
            html += body
            html += '</article>'
            html += '</body>'
            html += '</html>'

        return html, body


class GithubCompiler(Compiler):
    default_css = "github.css"

    def curl_convert(self, data):
        try:
            import subprocess

            # It looks like the text does NOT need to be escaped and
            # surrounded with double quotes.
            # Tested in ubuntu 13.10, python 2.7.5+
            shell_safe_json = data.decode('utf-8')
            curl_args = [
                'curl',
                '-H',
                'Content-Type: application/json',
                '-d',
                shell_safe_json,
                'https://api.github.com/markdown'
            ]

            github_oauth_token = self.settings.get('github_oauth_token')
            if github_oauth_token:
                curl_args[1:1] = [
                    '-u',
                    github_oauth_token
                ]

            markdown_html = subprocess.Popen(curl_args, stdout=subprocess.PIPE).communicate()[0].decode('utf-8')
            return markdown_html
        except subprocess.CalledProcessError:
            sublime.error_message('cannot use github API to convert markdown. SSL is not included in your Python installation. And using curl didn\'t work either')
        return None

    def preprocessor_critic(self, text):
        ''' Stip out multi-markdown critic marks.  Accept changes by default '''
        return CriticDump().dump(text, self.settings.get("strip_critic_marks", "accept") == "accept")

    def parser_specific_preprocess(self, text):
        if self.settings.get("strip_critic_marks", "accept") in ["accept", "reject"]:
            text = self.preprocessor_critic(text)
        return text

    def parser_specific_postprocess(self, html):
        ''' Post-processing for github API '''

        if self.settings.get("github_inject_header_ids", False):
            html = self.postprocess_inject_header_id(html)
        return html

    def postprocess_inject_header_id(self, html):
        ''' Insert header ids when no anchors are present '''
        unique = {}

        def header_to_id(text):
            if text is None:
                return ''
            # Strip html tags and lower
            id = RE_TAGS.sub('', text).lower()
            # Remove non word characters or non spaces and dashes
            # Then convert spaces to dashes
            id = RE_WORD.sub('', id).replace(' ', '-')
            # Encode anything that needs to be
            return quote(id)

        def inject_id(m):
            id = header_to_id(m.group('text'))
            if id == '':
                return m.group(0)
            # Append a dash and number for uniqueness if needed
            value = unique.get(id, None)
            if value is None:
                unique[id] = 1
            else:
                unique[id] += 1
                id += "-%d" % value
            return m.group('open')[:-1] + (' id="%s">' % id) + m.group('text') + m.group('close')

        RE_TAGS = re.compile(r'''</?[^>]*>''')
        RE_WORD = re.compile(r'''[^\w\- ]''')
        RE_HEADER = re.compile(r'''(?P<open><h([1-6])>)(?P<text>.*?)(?P<close></h\2>)''', re.DOTALL)
        return RE_HEADER.sub(inject_id, html)

    def parser_specific_convert(self, markdown_text):
        ''' convert input markdown to HTML, with github or builtin parser '''

        markdown_html = _CANNOT_CONVERT
        github_oauth_token = self.settings.get('github_oauth_token')

        # use the github API
        sublime.status_message('converting markdown with github API...')
        github_mode = self.settings.get('github_mode', 'gfm')
        data = {
            "text": markdown_text,
            "mode": github_mode
        }
        data = json.dumps(data).encode('utf-8')

        def get_github_response_from_exception(e):
            body = json.loads(e.read().decode('utf-8'))
            return 'GitHub\'s original response: (HTTP Status Code %s) "%s"' % (e.code, body['message'])

        try:
            headers = {
                'Content-Type': 'application/json'
            }
            if github_oauth_token:
                headers['Authorization'] = "token %s" % github_oauth_token
            url = "https://api.github.com/markdown"
            sublime.status_message(url)
            request = Request(url, data, headers)
            markdown_html = urlopen(request).read().decode('utf-8')
        except HTTPError as e:
            if e.code == 401:
                error_message = 'GitHub API authentication failed. Please check your OAuth token.\n\n'
                sublime.error_message(error_message + get_github_response_from_exception(e))
            elif e.code == 403: # Forbidden
                message = "It seems like you have exceeded GitHub\'s API rate limit.\n\n"
                message += "To continue using GitHub's markdown format with this package, log in to "
                message += "GitHub, then go to Settings > Personal access tokens > Generate new token, "
                message +=" copy the token's value, and paste it in this package's user settings under the key "
                message += "'github_oauth_token'. Example:\n\n"
                message += "{\n\t\"github_oauth_token\": \"xxxx....\"\n}\n\n"""
                sublime.error_message(message + get_github_response_from_exception(e))
            else:
                error_message = 'GitHub API responded in an unfriendly way.\n\n'
                sublime.error_message(error_message + get_github_response_from_exception(e))
        except URLError:
            # Maybe this is a Linux-install of ST which doesn't bundle with SSL support
            # So let's try wrapping curl instead
            markdown_html = self.curl_convert(data)
        except:
            e = sys.exc_info()[1]
            print(e)
            traceback.print_exc()
            sublime.error_message('Cannot use GitHub\'s API to convert Markdown. Please check your settings.\n\n' + get_github_response_from_exception(e))
        else:
            sublime.status_message('converted markdown with github API successfully')

        return markdown_html


class ExternalMarkdownCompiler(Compiler):
    default_css = "markdown.css"

    def __init__(self, parser):
        """Initialize."""

        self.parser = parser
        super(ExternalMarkdownCompiler, self).__init__()

    def parser_specific_convert(self, markdown_text):
        import subprocess
        settings = sublime.load_settings("MarkdownPreview.sublime-settings")
        binary = settings.get('markdown_binary_map', {})[self.parser]

        if len(binary) and os.path.exists(binary[0]):
            cmd = binary
            sublime.status_message('converting markdown with %s...' % self.parser)
            if sublime.platform() == "windows":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                p = subprocess.Popen(
                    cmd, startupinfo=startupinfo,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
            else:
                p = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
            for line in markdown_text.split('\n'):
                p.stdin.write((line + '\n').encode('utf-8'))
            markdown_html = p.communicate()[0].decode("utf-8")
            if p.returncode:
                # Log info to console
                sublime.error_message("Could not convert file! See console for more info.")
                print(markdown_html)
                markdown_html = _CANNOT_CONVERT
        else:
            sublime.error_message("Cannot find % binary!" % self.binary)
            markdown_html = _CANNOT_CONVERT
        return markdown_html


class MarkdownCompiler(Compiler):
    default_css = "markdown.css"

    def set_highlight(self, pygments_style, css_class):
        ''' Set the Pygments css. '''

        if pygments_style and not self.noclasses:
            style = None
            if pygments_style not in pygments_local:
                try:
                    style = get_formatter_by_name('html', style=pygments_style).get_style_defs('.codehilite pre')
                except Exception:
                    pygments_style = 'github'
            if style is None:
                style = load_resource(pygments_local[pygments_style]) % {
                    'css_class': ''.join(['.' + x for x in css_class.split(' ') if x])
                }

            self.pygments_style = '<style>%s</style>' % style
        return pygments_style

    def get_highlight(self):
        ''' return the Pygments css if enabled. '''
        return self.pygments_style if self.pygments_style else ''

    def preprocessor_critic(self, text):
        ''' Stip out multi-markdown critic marks.  Accept changes by default '''
        return CriticDump().dump(text, self.settings.get("strip_critic_marks", "accept") == "accept")

    def parser_specific_preprocess(self, text):
        if self.settings.get("strip_critic_marks", "accept") in ["accept", "reject"]:
            text = self.preprocessor_critic(text)
        return text

    def process_extensions(self, extensions):
        re_pygments = re.compile(r"(?:\s*,)?pygments_style\s*=\s*([a-zA-Z][a-zA-Z_\d]*)")
        re_pygments_replace = re.compile(r"pygments_style\s*=\s*([a-zA-Z][a-zA-Z_\d]*)")
        re_use_pygments = re.compile(r"use_pygments\s*=\s*(True|False)")
        re_insert_pygment = re.compile(r"(?P<bracket_start>codehilite\([^)]+?)(?P<bracket_end>\s*\)$)|(?P<start>codehilite)")
        re_no_classes = re.compile(r"(?:\s*,)?noclasses\s*=\s*(True|False)")
        re_css_class = re.compile(r"css_class\s*=\s*([\w\-]+)")
        # First search if pygments has manually been set,
        # and if so, read what the desired color scheme to use is
        self.pygments_style = None
        self.noclasses = False
        use_pygments = True
        pygments_css = None

        count = 0
        for e in extensions:
            if e.startswith("codehilite"):
                m = re_use_pygments.search(e)
                use_pygments = True if m is None else m.group(1) == 'True'
                m = re_css_class.search(e)
                css_class = m.group(1) if m else 'codehilite'
                pygments_style = re_pygments.search(e)
                if pygments_style is None:
                    pygments_css = "github"
                    m = re_insert_pygment.match(e)
                    if m is not None:
                        if m.group('bracket_start'):
                            start = m.group('bracket_start') + ',pygments_style='
                            end = ")"
                        else:
                            start = m.group('start') + "(pygments_style="
                            end = ')'

                        extensions[count] = start + pygments_css + end
                else:
                    pygments_css = pygments_style.group(1)

                # Set the style, but erase the setting if the CSS is pygments_local.
                # Don't allow 'no_css' with non internal themes.
                # Replace the setting with the correct name if the style was invalid.
                original = pygments_css
                pygments_css = self.set_highlight(pygments_css, css_class)
                if pygments_css in pygments_local:
                    extensions[count] = re_no_classes.sub('', re_pygments.sub('', e))
                elif original != pygments_css:
                    extensions[count] = re_pygments_replace.sub('pygments_style=%s' % pygments_css, e)

                noclasses = re_no_classes.search(e)
                if noclasses is not None and noclasses.group(1) == "True":
                    self.noclasses = True
            count += 1

        # Second, if nothing manual was set, see if "enable_highlight" is enabled with pygment support
        # If no style has been set, setup the default
        if (
            pygments_css is None and
            self.settings.get("enable_highlight") is True
        ):
            pygments_css = self.set_highlight('github', 'codehilite')
            guess_lang = str(bool(self.settings.get("guess_language", True)))
            use_pygments = bool(self.settings.get("enable_pygments", True))
            extensions.append(
                "codehilite(guess_lang=%s,use_pygments=%s)" % (
                    guess_lang, str(use_pygments)
                )
            )

        if not use_pygments:
            self.pygments_style = None

        # Get the base path of source file if available
        base_path = self.settings.get('builtin').get("basepath")
        if base_path is None:
            base_path = ""

        # Replace BASE_PATH keyword with the actual base_path
        return [e.replace("${BASE_PATH}", base_path) for e in extensions]

    def get_config_extensions(self, default_extensions):
        config_extensions = self.settings.get('enabled_extensions')
        if not config_extensions or config_extensions == 'default':
            return self.process_extensions(default_extensions)
        if 'default' in config_extensions:
            config_extensions.remove('default')
            config_extensions.extend(default_extensions)
        return self.process_extensions(config_extensions)

    def parser_specific_convert(self, markdown_text):
        sublime.status_message('converting markdown with Python markdown...')
        config_extensions = self.get_config_extensions(DEFAULT_EXT)
        md = Markdown(extensions=config_extensions)
        html_text = md.convert(markdown_text)
        # Retrieve the meta data returned from the "meta" extension
        self.settings.add_meta(md.Meta)
        return html_text


class MarkdownPreviewSelectCommand(sublime_plugin.TextCommand):
    def run(self, edit, target='browser'):

        settings = sublime.load_settings("MarkdownPreview.sublime-settings")
        md_map = settings.get('markdown_binary_map', {})
        parsers = [
            "markdown",
            "github"
        ]

        # Add external markdown binaries.
        for k in md_map.keys():
            parsers.append(k)

        self.target = target

        enabled_parsers = set()
        for p in settings.get("enabled_parsers", ["markdown", "github"]):
            if p in parsers:
                enabled_parsers.add(p)

        self.user_parsers = list(enabled_parsers)
        self.user_parsers.sort()

        window = self.view.window()
        length = len(self.user_parsers)
        if window is not None and length:
            if length == 1:
                self.view.run_command(
                    "markdown_preview",
                    {
                        "parser": self.user_parsers[0],
                        "target": self.target
                    }
                )
            else:
                window.show_quick_panel(self.user_parsers, self.run_command)

    def run_command(self, value):
        if value > -1:
            self.view.run_command(
                "markdown_preview",
                {
                    "parser": self.user_parsers[value],
                    "target": self.target
                }
            )


class MarkdownPreviewCommand(sublime_plugin.TextCommand):
    def run(self, edit, parser='markdown', target='browser'):


        settings = sublime.load_settings('MarkdownPreview.sublime-settings')

        # backup parser+target for later saves
        self.view.settings().set('parser', parser)
        self.view.settings().set('target', target)

        if parser == "github":
            compiler = GithubCompiler()
        elif parser == 'markdown':
            compiler = MarkdownCompiler()
        elif parser in settings.get("enabled_parsers", ("markdown", "github")):
            compiler = ExternalMarkdownCompiler(parser)
        else:
            # Fallback to Python Markdown
            compiler = MarkdownCompiler()

        # #################################################################
        # ######################################## @@@ MDB (07-02-2017) ###
        # #################################################################

        # If a *.sublime-project file exists within base directory of calling markdown file,
        # skip chapter compile modifications!
        DO_CHAPTER_COMPILE = 0

        if self.view.file_name() != None:
            DO_CHAPTER_COMPILE = settings.get("chapter_compile")
            if DO_CHAPTER_COMPILE:
                for file in os.listdir(os.path.dirname(self.view.file_name())):
                    if file.endswith(".sublime-project"):
                        print("Sublime project file", file, "found, skip chapter compile modifications!")
                        osd.info("Sublime project file %s ".format(file) + "found, skip chapter compile modifications!").send()
                        DO_CHAPTER_COMPILE = 0
                        break
            if DO_CHAPTER_COMPILE:
                self.temp_toc_refs(edit, 'insert')

        # Check if PlantUML inline diagram rendering is enabled in user settings.
        if settings.get("inline_diagram"):
            procInline = False
            inlineUml = InlineUmlDiagram(self.view)

            # Process inline replacements
            procInline = inlineUml.process(self.view, edit)

        # Check if external code import feature is enabled in user settings.
        if settings.get("code_import"):
            dprint('code importer')
            codeImport = CodeImport(self.view, edit)
            codeImport.process(self.view, edit)

        # Check if validate-title feature is enabled in user settings.
        if settings.get("validate_title"):
            _title=validate_title(self.view)
            if _title != None :
                osd.ok('Validate Title', "Filename: {}\nTitle: {}".format(
                    os.path.basename(self.view.file_name()), _title))
            else:
                osd.crit('Validate Title', 'No title meta data found!')

        # #################################################################
        # Invoke markdown compiler
        # #################################################################
        html, body = compiler.run(self.view, preview=(target in ['disk', 'browser']))
        # #################################################################

        if settings.get("paragraph_numbering") == None:
            print("%s on %s returns: None" 
                % (os.path.basename(__file__), 'settings.get("paragraph_numbering")'))
        else:
            if settings.get("paragraph_numbering"):
                html = self.addParagraphNumbering(html)

        # Check if external code import feature is enabled in user settings.
        if settings.get("code_import"):
            if codeImport:
                codeImport.unprocess()

        # Check if PlantUML inline diagram rendering is enabled in user settings.
        if settings.get("inline_diagram"):
            if procInline:
                inlineUml.unprocess()

        # Check if article footer attribute exists in user settings
        if settings.has("make_article_footer") & settings.has("article_footer"):
            if settings.get("make_article_footer"):
                meta = getHtmlMetaInfo(html)
                pp(meta)
                if 'author' in meta.keys():
                    try:
                        hf = AddHeaderFooter(html, meta)
                        html = hf.modified_html()
                        del hf
                    except:
                        osd.warn('Error while processing "make_article_footer"').send()
                else:
                    osd.warn('No "author" meta data found, abort "make_article_footer"').send()

        # Check if feature "!DISABLE href in TOC" is enabled 
        if settings.get("disable_href_in_toc")["enable_feature"]:
            keySeqs = settings.get("disable_href_in_toc")["key_sequences"]
            start, stop, tocStr = self.getModifiedTocBlock(html, keySeqs)
            html = html[0:start-1] + tocStr + html[stop-1:-1]

        if DO_CHAPTER_COMPILE:
            self.temp_toc_refs(edit, 'clear')

        # #################################################################
        # #################################################################
        # #################################################################
        #
        if target in ['disk', 'browser']:
            # do not use LiveReload unless autoreload is enabled
            if settings.get('enable_autoreload', True):
                # check if LiveReload ST2 extension installed and add its script to the resulting HTML
                livereload_installed = ('LiveReload' in os.listdir(sublime.packages_path()))
                # build the html
                if livereload_installed:
                    port = sublime.load_settings('LiveReload.sublime-settings').get('port', 35729)
                    html += '<script>document.write(\'<script src="http://\' + (location.host || \'localhost\').split(\':\')[0] + \':%d/livereload.js?snipver=1"></\' + \'script>\')</script>' % port
            # update output html file
            tmp_fullpath = getTempMarkdownPreviewPath(self.view)
            save_utf8(tmp_fullpath, html)
            # now opens in browser if needed
            if target == 'browser':
                self.__class__.open_in_browser(tmp_fullpath, settings.get('browser', 'default'))
        elif target == 'sublime':
            # create a new buffer and paste the output HTML
            embed_css = settings.get('embed_css_for_sublime_output', True)
            if embed_css:
                new_view(self.view.window(), html, scratch=True)
            else:
                new_view(self.view.window(), body, scratch=True)
            sublime.status_message('Markdown preview launched in sublime')
        elif target == 'clipboard':
            # clipboard copy the full HTML
            sublime.set_clipboard(html)
            sublime.status_message('Markdown export copied to clipboard')
        elif target == 'save':
            save_location = compiler.settings.get('builtin').get('destination', None)
            if save_location is None:
                save_location = self.view.file_name()
                if save_location is None or not os.path.exists(save_location):
                    # Save as...
                    v = new_view(self.view.window(), html)
                    if v is not None:
                        v.run_command('save')
                else:
                    # Save
                    htmlfile = os.path.splitext(save_location)[0] + '.html'
                    save_utf8(htmlfile, html)
            else:
                save_utf8(save_location, html)

    @classmethod
    def open_in_browser(cls, path, browser='default'):
        if browser == 'default':
            if sys.platform == 'darwin':
                # To open HTML files, Mac OS the open command uses the file
                # associated with .html. For many developers this is Sublime,
                # not the default browser. Getting the right value is
                # embarrassingly difficult.
                import shlex
                import subprocess
                env = {'VERSIONER_PERL_PREFER_32_BIT': 'true'}
                raw = """perl -MMac::InternetConfig -le 'print +(GetICHelper "http")[1]'"""
                process = subprocess.Popen(shlex.split(raw), env=env, stdout=subprocess.PIPE)
                out, err = process.communicate()
                default_browser = out.strip().decode('utf-8')
                cmd = "open -a '%s' %s" % (default_browser, path)
                os.system(cmd)
            else:
                desktop.open(path)
            sublime.status_message('Markdown preview launched in default browser')
        else:
            cmd = '"%s" %s' % (browser, path)
            if sys.platform == 'darwin':
                cmd = "open -a %s" % cmd
            elif sys.platform == 'linux2':
                cmd += ' &'
            elif sys.platform == 'win32':
                cmd = 'start "" %s' % cmd
            result = os.system(cmd)
            if result != 0:
                sublime.error_message('cannot execute "%s" Please check your Markdown Preview settings' % browser)
            else:
                sublime.status_message('Markdown preview launched in %s' % browser)

    def getModifiedTocBlock(self, html, keySeq):
        tocStartIdx = html.find('<div class="toc">')
        tocEndIdx = html.find('</div>', tocStartIdx+1)
        
        if tocStartIdx < 0 or tocEndIdx < 0:
            return None

        _s=[]
        m=re.compile(''.join([
            '(.*)',
            '(<a href\=\"#.*\">)',
            '(\{})',
            '(.*)',
            '(<\/a>)',
            '(.*$)']).format(keySeq))
        for l in html[tocStartIdx:tocEndIdx].splitlines():
            try:
                gr = m.findall(l)[0]
               
                if len(gr) >= 5:
                    _s.append(''.join([
                        gr[0], '<span style="color: #959595;">',
                        gr[3] , '</span>', gr[5]]))
            except:
                _s.append(l)

        return (tocStartIdx, tocEndIdx, '\n'.join(_s))

    def temp_toc_refs(self, edit, mode='insert'):
        TOC_STRING="<!-- temp toc -->\n[TOC]\n\n"
        insertPos=None

        # Load ref chapter
        refBuf = self.load_refs(r"(.*(quellen|reference).*\.mm?d)")

        # fd = open("/tmp/MarkdownPreview.refBuff", "w+")
        # fd.writelines(refBuf)
        # fd.close()

        # refBuf = [w.replace('=C2=A0', '') for w in refBuf]

        # fd = open("/tmp/MarkdownPreview.refBuffenc", "w+")
        # fd.writelines(refBuf)
        # fd.close()

        print('Process "temp_toc_refs" with mode="{}" ...'.format(mode))

        #################################################################
        # print(refBuf)
        if mode == 'insert':
            ## Insert TOC and reference chapter
            #################################################################
            if refBuf != None:
                tmp = self.view.find(refBuf[0], 0, sublime.LITERAL)
                if tmp.begin() >= 0:
                    # If a reference "chapter" already exists, find region to
                    # be overwritten by the actual refBuf contents.
                    # Store insert position for new reference chapter
                    insertPos = self.temp_toc_refs(edit, 'clear')

                if insertPos == None:
                    insertPos = self.view.size()

                # Temporary append the reference chapter lines.
                self.view.insert(edit, insertPos, "" + "".join(refBuf))
            else:
                print("\nNo \"Quellenangaben*.mmd\" found!")

            # Insert temporary [TOC]
            self.view.insert(edit, 0, TOC_STRING)
            return

        if mode == 'clear':
            ## Remove TOC and reference chapter
            #################################################################
            if refBuf != None:
                # Search the first line of reference file buffer within current view.
                tmp = self.view.find(refBuf[0], 0, sublime.LITERAL)
                if tmp.begin() >= 0:
                    # If a reference "chapter" already exists, find region to
                    # be overwritten by the actual refBuf contents.
                    regErase = sublime.Region(tmp.begin(), self.view.size())
                    # Remove old reference chapter.
                    self.view.erase(edit, regErase)
                    # Store insert position for new reference chapter
            else:   
                tmp=None
            # Erase temporary TOC.
            while self.view.find(TOC_STRING, 0, sublime.LITERAL).begin() >= 0:
                self.view.erase(edit, self.view.find(TOC_STRING, 0, sublime.LITERAL))

            # Return region which has possibly been cleared.
            return tmp

    def addParagraphNumbering(self, html):
        dprint('Process "addParagraphNumbering" ...')
        html = html.replace('article class="markdown-body"', 'article class="markdown-body paragraphNum"')
        return html

    ##
    ## @brief      Loads references from references chapter markdown file.
    ##
    ## @param      self          this.
    ## @param      refFilePatt   Regex pattern for references chapter file name.
    ##
    ## @return     String buffer or None
    ##
    def load_refs(self, refFilePatt=r"(.*quellen.*\.mm?d)"):
        mdFileDir=os.path.dirname(self.view.file_name())
        refFile=None

        # Search pwd for a markdown file "references" (Quellenangaben).
        for file in os.listdir(os.path.dirname(self.view.file_name())):
            m=re.match(refFilePatt, file, re.I)
            if m == None:
                continue
            else:
                refFile=os.path.join(mdFileDir, m.group(0))

        # refBuf = self.load_refs_from_file(edit, refFile)
        if refFile != None:
            # If a reference file has been found, read contents to line buffer.
            fd = open(refFile, "r+")
            refBuf = fd.readlines();
            fd.close();
            return refBuf
        else:
            return None


class MarkdownBuildCommand(sublime_plugin.WindowCommand):
    def init_panel(self):
        if not hasattr(self, 'output_view'):
            if is_ST3():
                self.output_view = self.window.create_output_panel("markdown")
            else:
                self.output_view = self.window.get_output_panel("markdown")

    def puts(self, message):
        message = message + '\n'
        if is_ST3():
            self.output_view.run_command('append', {'characters': message, 'force': True, 'scroll_to_end': True})
        else:
            selection_was_at_end = (len(self.output_view.sel()) == 1
                                    and self.output_view.sel()[0]
                                    == sublime.Region(self.output_view.size()))
            self.output_view.set_read_only(False)
            edit = self.output_view.begin_edit()
            self.output_view.insert(edit, self.output_view.size(), message)
            if selection_was_at_end:
                self.output_view.show(self.output_view.size())
            self.output_view.end_edit(edit)
            self.output_view.set_read_only(True)

    def run(self):
        view = self.window.active_view()
        if not view:
            return
        start_time = time.time()

        self.init_panel()

        settings = sublime.load_settings('MarkdownPreview.sublime-settings')
        parser = settings.get('parser', 'markdown')
        if parser == 'default':
            parser = 'markdown'

        target = settings.get('build_action', 'build')
        if target in ('browser', 'sublime', 'clipboard', 'save'):
            view.run_command("markdown_preview", {"parser": parser, "target": target})
            return

        show_panel_on_build = settings.get("show_panel_on_build", True)
        if show_panel_on_build:
            self.window.run_command("show_panel", {"panel": "output.markdown"})

        mdfile = view.file_name()
        if mdfile is None or not os.path.exists(mdfile):
            self.puts("Can't build an unsaved markdown file.")
            return

        self.puts("Compiling %s..." % mdfile)

        if parser == "github":
            compiler = GithubCompiler()
        elif parser == 'markdown':
            compiler = MarkdownCompiler()
        elif parser in settings.get("enabled_parsers", ("markdown", "github")):
            compiler = ExternalMarkdownCompiler(parser)
        else:
            compiler = MarkdownCompiler()

        html, body = compiler.run(view, True, preview=False)

        htmlfile = compiler.settings.get('builtin').get('destination', None)

        if htmlfile is None:
            htmlfile = os.path.splitext(mdfile)[0] + '.html'
        self.puts("        ->" + htmlfile)
        save_utf8(htmlfile, html)

        elapsed = time.time() - start_time
        if body == _CANNOT_CONVERT:
            self.puts(_CANNOT_CONVERT)
        self.puts("[Finished in %.1fs]" % (elapsed))
        sublime.status_message("Build finished")


from pprint import pprint as pp
from mdLibs import mdosd as osd
from sublime import error_message
from threading import Thread
from os.path import splitext
from tempfile import NamedTemporaryFile
from shutil import copy2, move
from datetime import datetime
import inspect, ast, json

# Try to import sublime_diagram_plugin
from sublime_diagram_plugin import diagram as diag

# class MarkdownFoldByLevelCommand(sublime_plugin.TextCommand):
#     def run(self, edit, level='current'):
#         view = self.view
#         if level == "current":
#             ''' Query current selected section depth '''
#             currentChapterDepth = self.reverseFind('^#+', 500)


class MarkdownFoldCommand(sublime_plugin.TextCommand):
    def run(self, edit, level=-1):
        view = self.view

        if level < 0:
            ''' Query current selected section depth '''
            theLevel = self.reverseFind('^#+', 500)
        else:
            theLevel = level

        ''' Query markdown header section positions in current view '''
        heads = self.getHeaderSections()
        ''' Replace current selected regions '''
        view.selection.clear()
        view.selection.add_all(heads[theLevel-1])
        ''' Toggle fold selected chapters '''
        view.run_command("fold_section")

    def getHeaderSections(self, depth=10):
        view = self.view
        sects=[]
        [sects.append(view.find_all('^' + '#'*k + ' ')) for k in range(1, depth)]
        return sects
        # return view.find_by_selector('meta.block-level.markdown') 

    def reverseFind(self, pattern='^#+', maxReverseLines=200):
        view = self.view

        try:
            if view.sel()[0].empty():
                view.run_command("expand_selection", {"to": "line"})            
        except (ValueError, IndexError):
            dprint('Nothing selected so reverse search from buffer end!')
            view.selection.add(view.__len__())            
            view.run_command("expand_selection", {"to": "line"})            

        selected=[]

        for k in range(1, maxReverseLines + 1):
            ''' End of buffer reached? '''
            if view.sel()[0] == selected:
                break
            selected = view.sel()[0]

            if re.findall(pattern,view.substr(view.line(selected))):
                break
            view.selection.add(view.line(view.sel()[0].begin()-1))

        try:
            return len(re.findall('^#+', view.substr(view.line(view.sel()[0])))[0])
        except (ValueError,IndexError):
            if k == maxReverseLines:
                dprint('No matches for reverse finding pattern \'{}\' '.format(pattern) +
                    'within the last {} lines!'.format(maxReverseLines))
            else:
                dprint('No matches for reverse find pattern "{}"'.format(pattern))
            return int(-1)


class AddHeaderFooter(object):
    """docstring for AddHeaderFooter"""

    def __init__(self, html, meta):
        self.footer = [ \
        '<center>'
        '<div style="width: 95%">'
        '<hr id="hr_footer">'
        '<table style="width: 100%"><tr>',
        '</tr>'
        '</table>'
        '<hr id="hr_header">'
        '</div>'
        '</center>' ]

        self.colTemplate = '<td>{}</td>\n'
        self.html = html
        self.row = []

        arg=[
        meta['title'],
        meta['author'],
        meta['date'],
        meta['location'],
        {'icon': meta['footicon'], 'width': "55px"}]

        # try:
        if str(type(arg).__name__) == 'list':
            for k in range(len(arg)):
                # Check if [.., .., .., [.., .., ], ...]
                if type(arg[k]) == type(dict()):
                    if 'width' in arg[k].keys():
                        icoWidth = arg[k]['width']
                    else:
                        icoWidth = '50px'

                    if 'icon' in arg[k].keys():
                        self.row.append(
                            '<td width="{1}">'
                            '<img style="display: inline; height: {1}; float: right;" '
                            'src="{0}"/></td>\n'.format(arg[k]['icon'], icoWidth))

                elif k==0:
                    self.row.append('<td style="text-align: left;">{}</td>\n'.format(arg[k]))
                elif k==(len(arg)-1):
                    self.row.append('<td style="text-align: right;">{}</td>\n'.format(arg[k]))
                else:
                    self.row.append('<td style="text-align: center;">{}</td>\n'.format(arg[k]))
        # elif str(type(arg).__name__) == 'dict':
        #     if 'width' in arg.keys():
        #         icoWidth = arg['width']
        #     else:
        #         icoWidth = '50px'

        #     if 'icon' in arg.keys():
        #         osd.info('icon found').send()

        #         self.row.append(
        #             '<td width="{1}">'
        #             '<img style="display: inline; height: {1}; float: right;" '
        #             'src="{0}"/></td>\n'.format(arg['icon'], icoWidth))
        #     elif :
        #         self.row.append('<td style="text-align: center;">{}</td>\n'.format(arg[k]))


        elif str(type(arg).__name__) == 'str':
            self.row.append(arg)
        else:
            self.footer = ['ERROR']

        self.footer.insert(1, ''.join(self.row))

        # except:
        #     osd.info('except').send()
        #     dprint('Error while generate footer string')
        #     return

        # Create Header
        if '<body>' in self.html:
            key='<body>'
            idxStart = int(self.html.find(key))
            idxEnd = idxStart + len(key)
            _footer = [w.replace('<hr id="hr_footer">', '') for w in self.footer]

            self.html = self.html[:idxEnd] + ''.join(_footer) + \
                self.html[idxEnd:len(self.html)]

        # Create Footer
        if '</article>' in self.html:
            key='</article>'
            idxStart = int(self.html.find(key))
            idxEnd = idxStart + len(key)
            _footer = [w.replace('<hr id="hr_header">', '') for w in self.footer]

            self.html = self.html[:idxEnd] + ''.join(_footer) + \
                self.html[idxEnd:len(self.html)]

    def modified_html(self):
        return self.html


class CodeImportBlock(object):
    def __init__(self, keyRegStr, attrsRegStr, adjRows, mdFileDirname):
        settings = sublime.load_settings('MarkdownPreview.sublime-settings')
        self.keyReg = keyRegStr[0]
        self.keyStr = keyRegStr[1]
        self.attrsReg = attrsRegStr[0]
        self.adjacentRows = adjRows
        self.attrs=dict()

        # if self.attrsReg:
        if type(attrsRegStr[1]).__name__ == 'list':
            self.attrsStr = attrsRegStr[1]
        else:
            self.attrsStr = [attrsRegStr[1]]
        # else:
        #     self.attrsStr = None

        self.attrs.update({"path":
            self.keyStr.split(':')[1].strip('[\" ]').lstrip('[.\/]')})

        # Check for optional attributes
        if self.attrsReg:
            for attr in self.attrsStr:
                if '@codeimport_nocomments' in attr:
                    self.attrs.update({"comments": "remove"})

        sourceFile = os.path.join(mdFileDirname, self.attrs["path"])
        dprint('sourceFile: ', sourceFile)

        if not os.path.exists(sourceFile):
            osd.crit('Path {} doesn\'t exist'.format(sourceFile)).send()
            return

        fd = open(sourceFile, 'r+')
        if fd == None:
            osd.crit('File descriptor open returns None').send()
            return

        self.codeLines = fd.readlines()
        fd.close()

        # If no fancy-code markers found in adjacent lines, append them
        p=re.compile('^\`\`\`.*')

        if not len(p.findall(adjRows[0])) * len(p.findall(adjRows[1])):
            try:
                self.codeLines.insert(0, settings.get(
                    "code_import_default_fancy_marker") + '\n')
            except:
                self.codeLines.insert(0, '```\n')
            self.codeLines.append('```\n')


class CodeImport(object):
    ATTRPREFIXES=['@codeimport:', '@codeimport_nocomments']
    mdFileDirname=''

    """docstring for ImportExtSrcs"""
    def __init__(self, view, edit):
        if view.file_name() == None:
            view.run_command('save')
            dprint('Only saved files can be processed!')
            return

        self.view = view
        self.edit = edit
        self.mdFileDirname=os.path.dirname(view.file_name())
        self.blkObj=[]

        ''' Start to import code blocks '''
        dprint('Invoke "code import" processing...')

        keyRegs = [self.view.line(keyr) for keyr in self.view.find_all(
            self.ATTRPREFIXES[0])]

        # Filter all regions with scope comment.block.html  
        keyRegs = list(filter(lambda x: 'comment.block.html' not in 
            self.view.scope_name(x.begin()), keyRegs))

        for key in keyRegs:
            ''' Check for optional attributes.'''
            attrStrs = []
            attrCtr = 0
            attrs = self.view.line(key.end()+1)

            if self.ATTRPREFIXES[1] not in \
                self.view.substr(self.view.line(attrs.end()+attrCtr)):
                attrs = []
            else:
                while self.ATTRPREFIXES[1] in \
                    self.view.substr(self.view.line(attrs.end()+attrCtr)):
                    attrStrs.append(self.view.substr(self.view.line(attrs.end()+attrCtr)))
                    attrs = attrs.cover(self.view.line(attrs.end()+attrCtr))
                    attrCtr += 1

            if attrStrs:
                # Test if the @codeimport attribute is surrounded by fancy-code markers ```
                attrsRegStr_=[attrs, self.view.substr(attrs)]
                adjRows=(
                    self.view.substr(self.view.line(key.begin()-1)),
                    self.view.substr(self.view.line(attrs.end()+1))
                    )
            else:
                attrsRegStr_=[None, None]
                adjRows=(
                    self.view.substr(self.view.line(key.begin()-1)),
                    self.view.substr(self.view.line(key.end()+1))
                    )

            self.blkObj.append(
                CodeImportBlock(
                    keyRegStr=[key, self.view.substr(key)],
                    attrsRegStr=attrsRegStr_,
                    adjRows=adjRows,
                    mdFileDirname=self.mdFileDirname
                    )
                )

    def process(self, view, edit):
        print('Process "CodeImport" ...')
        # Create temporary backup of src file
        self.orig_src = create_temporary_copy(view, preserve_ext=True)

        # Store viewport position
        self.orig_viewport = self.view.viewport_position()

        for o in reversed(self.blkObj):
            # Remove optional attributes lines
            if o.attrsReg:
                self.view.erase(edit, self.view.full_line(o.attrsReg))

            # Join code lines
            code = ''.join(o.codeLines)

            # Remove comments if optional attribute @codeimport_nocomments found
            if 'comments' in o.attrs.keys():
                if o.attrs['comments'] == 'remove':
                    code = re.sub('(?s)\/\*.*?\*\/\n', '', code)
                    # code = re.sub('^\s*\/\/', '', code)
                    code = re.sub(' *\/\/[^\!].*\n', '', code)
                    # Remove multiple empty lines except of the first
                    code = re.sub('\n{3,}', '\n\n', code)

            view.replace(edit, o.keyReg, code)

    def unprocess(self):
        '''Restore original source file contents'''
        restore_temporary_copy(self.view, self.edit, self.orig_src)

        # Register a delayed restoreViewport callbac
        sublime.set_timeout(self.restoreViewport, 1)

    def restoreViewport(self):
        self.view.set_viewport_position(self.orig_viewport)


class UmlBlock(object):
    def __init__(self, blk_reg, blk_str='', blk_attr=None, diagram=None):
        self.blk_reg = blk_reg
        self.blk_str = blk_str
        self.attr_dict = blk_attr[0]
        self.attr_reg = blk_attr[1]
        self.diagram = diagram

    def get_replace_reg(self):
        if not self.blk_reg:
            dprint('Error, self.blk_reg is None')
            return None

        # If no diag attrs where given, replace uml block region
        if not self.attr_reg:
            return self.blk_reg

        attributes_region = self.attr_reg[0]
        for reg in self.attr_reg:
            attributes_region = attributes_region.cover(reg)

        # If diag attr found, cover regions of uml block and attr block
        return sublime.Region(
            self.blk_reg.begin(),
            self.blk_reg.cover(attributes_region).end()
            )


class InlineUmlDiagram(sublime_plugin.TextCommand):
    ATTRPREFIX='@diag_'
    '''
    Provides inline processing of PlantUML blocks. Uses sublime_diagram_plugin
    methods for inline UML block extraction and rendering. Additional inline
    diagram attributes can be used for html-image-tag customizations (i.e.
    scaling, style, alignment, ...). Each resulting markdown or html image tag,
    substitutes it's corresponding PlantUML block in the source view befor the
    Markdown Preview compiler call is processed. If not disabled in user
    settings, undo_inline_subst() method reverts the markdown document to the
    state before UML blocks has been processed inline.
    '''
    def process(self, view, edit):
        if self.view.file_name() == None:
            self.view.run_command('save')
            dprint('Only saved files can be processed!')
            return False

        self.edit = edit
        ''' Starts inline PlantUML block processing '''
        print('Invoke PlantUML inline diagram processing...')

        # Create temporary backup of src file
        # self.orig_src = self.create_temporary_copy(preserve_ext=True)
        self.orig_src = create_temporary_copy(view, preserve_ext=True)
        print('self.orig_src: ', self.orig_src.name)

        srcFile = 'untitled.txt'
        if view.file_name() is not None:
            srcFile = view.file_name()

        # Prepare sublime_diagram_plugin for Plant UML block extraction +
        # rendering process
        diag.setup()
        ACTIVE_PROCESSORS = diag.ACTIVE_PROCESSORS
        diags = []

        for processor in ACTIVE_PROCESSORS:
            blocks = []

            # Extract code blocks surrounded by ```
            cblocks = extract_code_blocks(self.view,)

            # Extract PlantUML code blocks via sublime_diagram_plugin methode
            puml_blocks = processor.extract_blocks(view)

            for block in puml_blocks:
                add = True
                for cblock in cblocks:
                    # Check if PlantUML block is implemented as fancy "code block"
                    if block.intersects(cblock):
                        add = False
                        break

                if add:
                    # Extract optional PlantUML diagram attributes
                    attrsObj = extract_attrs(self.view, block, self.ATTRPREFIX)
                    dprint('found %i diag attrs: ' % len(attrsObj[0]), attrsObj[0])

                    _b_= UmlBlock(
                        blk_reg=block,
                        blk_str=view.substr(block),
                        blk_attr=attrsObj,
                        )
                    blocks.append(_b_)
                    dprint('Blocks:')
                    pp(_b_.get_replace_reg())

            if blocks:
                diags.append((processor, blocks,))

        # Return if no PlantUML code block found
        if not diags:
            dprint('No PlantUML blocks found!')
            return False

        dprint('Found %i PlantUML blocks.' % len(diags[0][1]))

        # Process extracted PlantUML blocks via sublime_diagram_plugin
        diagram_files=[]
        for processor, uml_blocks in diags:
            diagram_files.extend(processor.process(
                sourceFile=splitext(srcFile)[0] + '-',
                text_blocks=[blk.blk_str for blk in uml_blocks]
                ))

        for k in range(len(diagram_files)):
            blk = uml_blocks[k]

            diagram_files[k].name = self.move_to_export_dir(diagram_files[k], k)

            blk.diagram = diagram_files[k]
            blk.line_count = (int(view.rowcol(blk.blk_reg.end())[0]) -
            int(view.rowcol(blk.blk_reg.begin())[0]))

        del diags, diagram_files

        # Generate markdown/html image tag and replace UML blocks temporary
        for blk in reversed(uml_blocks):
            img_tag = self.gen_image_tag(blk, self.get_default_style_dict())
            dprint(img_tag)
            view.replace(edit, blk.get_replace_reg(), img_tag)

        return True

    def unprocess(self):
        restore_temporary_copy(self.view, self.edit, self.orig_src)

    def move_to_export_dir(self, diagram_file, suffix):
        '''
        Move/rename diagram_file to export dir specified in settings
        "inline_diagram_export_dir"
        '''
        exportDir = sublime.load_settings(
            'MarkdownPreview.sublime-settings').get(
            'inline_diagram_export_dir')

        # Expand variables
        exportDir = sublime.expand_variables(exportDir,
            self.view.window().extract_variables())

        if not exportDir:
            exportDir = os.path.dirname(self.view.file_name())
        if not os.path.exists(exportDir):
            os.makedirs(exportDir)

        diagFilePath = os.path.join(exportDir,
            '{0}-diag_{2}{1}'.format(
                splitext(os.path.basename(self.view.file_name()))[0],
                splitext(os.path.basename(diagram_file.name))[1], suffix))

        # Move file diagFilePath to temp + timestring if exists
        if os.path.isfile(diagFilePath):
            move(diagFilePath, os.path.join(
                tempfile.gettempdir(), os.path.basename(diagFilePath) +
                datetime.now().strftime('%Y-%m-%d_%H%M%S_%f')))

        # Move diag file
        move(diagram_file.name, diagFilePath)
        return diagFilePath

    def get_default_style_dict(self):
        '''
        Returns dict for default style attributes, even if no
        "inline_diagram_default_style" has been set in user settings.
        '''
        defaultDict = sublime.load_settings(
            'MarkdownPreview.sublime-settings').get(
            'inline_diagram_default_style')

        # Check if user setting contains default markdown/html tag
        if type(defaultDict).__name__ != "dict":
            if not defaultDict:
                dprint('No "inline_diagram_default_style" declared in settings.')
            else:
                dprint('User settings "inline_diagram_default_style" has type "{0!s}", must be "dict".'.
                    format(type(defaultDict).__name__))

            defaultDict = {
            "src":   "",
            "class": "",
            "style": {},
            "title": "",
            }
        return defaultDict

    # def extract_diagram_attrs(self, block):
    #     '''
    #     Extracts optional attributes for diagram image/html tag customizations.
    #     Returns a tupel of sequential inline attributes as dict and their
    #     regions as list object.
    #     '''
    #     attr_regs = []
    #     jattrsObj = dict()

    #     # Find sequential related regions of @diag_ attribute lines
    #     next_line = self.view.line(block.end()+1)
    #     while '@diag_' in self.view.substr(next_line):
    #         attr_regs.append(next_line)
    #         next_line = self.view.line(attr_regs[len(attr_regs)-1].end()+1)

    #     for attr in attr_regs:
    #         # Split attr into dict key and value
    #         key=self.view.substr(attr).split(':', 1)[0].strip().replace('@diag_','')
    #         val=self.view.substr(attr).split(':', 1)[1].strip()

    #         # Check diagram attribute values for JSON compatibility.
    #         try:
    #             if key != "image_tag":
    #                 jattrsObj[key] = json.loads(val)
    #             else:
    #                 jattrsObj[key] = val
    #         except ValueError:
    #             print('ValueError occured while trying to json.load() diagram attrs')
    #             return dict(), list()

    #     return jattrsObj, attr_regs

    def gen_image_tag(self, uml_block, default_attrs):
        '''
        Generate the inline block replacement string in form of a html <img ../>
        tag. The diagram image html block can be customized by user settings
        default attributes "inline_diagram_default_style" or via inline digram
        attributes (See "inline diagram examples" section in package README).
        '''
        defas = default_attrs
        align = ''

        if 'src' not in defas.keys():
            defas.update({'src': ''})
        defas['src'] = '{0!s}'.format(uml_block.diagram.name)

        # If current uml_block has optional attributes dict...
        if uml_block.attr_dict:
            inlas = uml_block.attr_dict
            # Check for inline uml_block attributes
            if 'image_tag' in inlas.keys():
                return re.sub(r'\\n', '\n', re.sub(r'(^\"|\"$)', '',
                    inlas['image_tag'].
                    replace('src="%s"', 'src="{0!s}"'.format(defas['src']))))

            if 'style' not in inlas.keys():
                inlas.update({'style': dict({})})

            # Handle all style="..." sub attributes (like "float": "right" ...)
            for subAttr in list(filter(lambda x: x not in defas.keys(), inlas.keys())):
                if subAttr == 'align':
                    align = inlas.pop(subAttr).strip('"')
                else:
                    inlas['style'][subAttr] = inlas.pop(subAttr).strip('"')

            # Merge dicts "inline attributes" and "default attributes"
            for key in list(inlas.keys()):
                # Inline diagram attributes which are not present in defas dict and
                # are string types should be handled as style vps.
                if key not in defas.keys():
                    print('key %s (%s) not in defas' % (key, type(inlas[key]).__name__))
                    continue
                defas[key] = inlas.pop(key)
            del inlas

        imgAttrsStr=''
        for key in list(filter(lambda x: x not in ['style'], defas.keys())):
            imgAttrsStr += '{0!s}="{1!s}" '.format(key, defas[key])

        imgStyleStr = re.sub('[\{\}\"]','',
            json.dumps(defas['style'], separators=('; ',': ')))

        if len(imgStyleStr) > 0:
            imgStyleStr = '%s;' % imgStyleStr

        if align != '':
            if 'center' in align:
                return str('<center><img {0!s} style="{1!s}"/></center>'.format(imgAttrsStr, imgStyleStr))

        return str('<img {0!s} style="{1!s}"/>'.format(imgAttrsStr, imgStyleStr))

    # def create_temporary_copy(self, preserve_ext=False):
        '''
        Copies the source file into a temporary file. Returns a
        _TemporaryFileWrapper, whose destructor deletes the temp file (i.e. the
        temp file is deleted when the object goes out of scope).
        '''
        tf_suffix=''
        if preserve_ext:
            if self.view.file_name() != None:
                tf_suffix = splitext(self.view.file_name())[1]
            else:
                tf_suffix = 'None'
        tf = NamedTemporaryFile(suffix=tf_suffix, delete=False)
        save_utf8(tf.name, self.view.substr(sublime.Region(0, self.view.size())))
        return tf

    # def undo_inline_subst(self, edit):
    #     '''
    #     Undo inline replacements.
    #     '''
    #     self.view.replace(edit, sublime.Region(0, self.view.size()),
    #         load_utf8(self.orig_src.name))

def expanded_var(view, env_var):
    return sublime.expand_variables(env_var, 
        view.window().extract_variables())

def getHtmlMetaInfo(html):
    '''Returns meta information about the html document'''
    mMetas=re.findall('<meta name=\"(.*)\" +content=\"(.*)\"', html)

    try:
        mMetas.append(['title', re.findall('<title>(.*)<\/title>', html)[0]])
    except:
        mMetas.append(['title', 'NOTITLEFOUND'])

    meta=dict()
    [meta.update({m[0]: '{}'.format(m[1])}) for m in mMetas]
    return meta

def validate_title(view):
    # Read value-key pairs from markdown head
    try:
        linesReg = sublime.Region(0, view.find('\n[ \t]*\n', 0).begin() - 1)
        mdMeta=[list(filter(None, re.split('(^[a-zA-Z]+)\: *(.*) *$',
            view.substr(l)))) for l in view.lines(linesReg)]
        title = mdMeta[['Title' in m for m in mdMeta].index(True)][1]
    except:
        dprint('No title meta data found!')
        return None

    dprint("Filename: {}\nTitle: {}".format(
        os.path.basename(view.file_name()), title))
    return title

def create_temporary_copy(view, preserve_ext=True):
    '''
    Copies the source file into a temporary file. Returns a
    _TemporaryFileWrapper, whose destructor deletes the temp file (i.e. the
    temp file is deleted when the object goes out of scope).
    '''
    tf_suffix=''
    if preserve_ext:
        if view.file_name() != None:
            tf_suffix = splitext(view.file_name())[1]
        else:
            tf_suffix = 'None'
    tf = NamedTemporaryFile(suffix=tf_suffix, delete=False)
    save_utf8(tf.name, view.substr(sublime.Region(0, view.size())))
    return tf

def restore_temporary_copy(view, edit, tempFile):
    '''Undo inline replacements. '''
    view.replace(edit, sublime.Region(0, view.size()),
        load_utf8(tempFile.name))

def extract_code_blocks(view):
    '''
    Extract all code blocks surrounded by ``` and return it's region
    objects. If in a later step, some fancy code block ``` region intersect
    with the current processed PlantUML block region, skip inline processing
    of this intersecting UML block. As a result, the possibility for
    markdown fancy UML code blocks remains if no inline rendering is
    desired.
    '''
    ctags = view.find_all('^```.*')

    # Check for even count of code block tags ```
    if len(ctags) % 2 != 0:
        dprint('Warning, odd number of code-block tags ``` found!')
        return []

    return [sublime.Region.cover(
        ctags[k], ctags[k+1]) for k in range(0, len(ctags), 2)]

def extract_attrs(view, block, attr_prefix):
    '''
    Extracts optional attributes (@...) if the @<_prefix>xyz matches attr_prefix.
    Returns a tupel of sequential inline attributes as dict and their
    regions as list object.
    '''
    attr_regs = []
    jattrsObj = dict()

    # Find sequential related regions of @diag_ attribute lines

    next_line = view.line(block.end()+1)
    print(view.substr(next_line))
    while attr_prefix in view.substr(next_line):
        attr_regs.append(next_line)
        next_line = view.line(attr_regs[len(attr_regs)-1].end()+1)

    for attr in attr_regs:
        # Split attr into dict key and value
        key=view.substr(attr).split(':', 1)[0].strip().replace(attr_prefix,'')
        val=view.substr(attr).split(':', 1)[1].strip()

        # Check diagram attribute values for JSON compatibility.
        try:
            if key != "image_tag":
                jattrsObj[key] = json.loads(val)
            else:
                jattrsObj[key] = val
        except ValueError:
            print('ValueError occured while trying to json.load() diagram attrs')
            return dict(), list()

    return jattrsObj, attr_regs

def get_class_from_frame(fr):
    args, _, _, value_dict = inspect.getargvalues(fr)
    # we check the first parameter for the frame function is
    # named 'self'
    if len(args) and args[0] == 'self':
        # in that case, 'self' will be referenced in value_dict
        instance = value_dict.get('self', None)
        if instance:
            # return its class
            return getattr(instance, '__class__', None)
    # return None otherwise
    return None

def dprint(string, *args):
    '''
    Verbose level configureable debug print methode. Inspects the call stack to
    receive callers class name. Class dependent verbosity levels can be
    configured via user settings file.
    "debug_levels": { "<class name>": <level> }
    '''

    # frame = inspect.stack()[1][0]
    # return
    # print(get_class_from_frame(frame))

    try:
        caller_class = inspect.stack()[1][0].f_locals["self"].__class__.__name__
        caller_method = inspect.stack()[1][0].f_code.co_name

        debug_levels = sublime.load_settings(
            'MarkdownPreview.sublime-settings').get('debug_levels')
        if not debug_levels or caller_class not in debug_levels.keys():
            return

        # debug_levels={caller_class:2}

        # Only print string from classes with debug level > 1
        if debug_levels[caller_class] > 1:
            print('[ {0!s}.{1!s} ]: {2!s}'.format(
                caller_class,
                caller_method,
                string), end=' ')
            if args:
                for arg in args:
                    print(arg, end=' ')
            print(' ')
    except:
        if not args:
            print('dprint except: ', string)
        else:
            print('dprint except: ' + string, args)



    # def query_export_dir(self):
    #     '''
    #     Query a valid export directory for png files.
    #     '''
    #     exportDir = sublime.load_settings(
    #         'MarkdownPreview.sublime-settings').get(
    #         'inline_diagram_export_dir')

    #     # Expand variables
    #     exportDir = sublime.expand_variables(exportDir,
    #         self.view.window().extract_variables())

    #     # If no export dir given in user settings, export to current dir name.
    #     if not exportDir:
    #         # Save current buffer if it doesn't exist on filesystem
    #         if self.view.file_name() == None:
    #             buffName = re.findall('\w+', self.view.substr(
    #                 self.view.find('^#+\s(\w+)',0)))[0]
    #             if buffName != []:
    #                 self.view.set_name(buffName)
    #             else:
    #                 self.view.set_name('untitled')
    #             self.view.run_command('save')
    #             if self.view.file_name() == None:
    #                 raise

    #             self.view.set
    #         exportDir = os.path.dirname(self.view.file_name())

    #     if not os.path.exists(exportDir):
    #         os.makedirs(exportDir)

    # def safeMakedirs(self, path):
    #     try:
    #         os.makedirs(path)
    #     except OSError as e:
    #         if e.errno == errno.EACCES:
    #             alterDir = self.view.run_command('prompt_save_as')
    #             if isWritable(alterDir):
    #                 os.makedirs(path)
    #             else:
    #                 e.filename = path
    #                 raise
    #     return True

    # def isWritable(path):
    #     '''
    #     Test if a given path is writeable.
    #     '''
    #     try:
    #         testfile = tempfile.TemporaryFile(dir = path)
    #         testfile.close()
    #     except OSError as e:
    #         if e.errno == errno.EACCES:  # 13
    #             return False
    #         e.filename = path
    #         raise
    #     return True