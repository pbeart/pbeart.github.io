import glob, os, shutil, datetime, subprocess, re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import xml.etree.ElementTree as etree
import argparse

import jinja2, markdown, dateutil
from markdown.inlinepatterns import InlineProcessor, Pattern
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

IS_DEPLOYMENT = "CI" in os.environ

@dataclass
class Post:
    title: str
    url: str
    extract: str
    published: datetime.datetime
    html: str
    updated: Optional[datetime.datetime] = None
    math_enabled: bool = False

template_loader = jinja2.FileSystemLoader(searchpath="./templates")
template_env = jinja2.Environment(loader=template_loader)

template_env.globals["mlink"] = template_env.from_string("""{% macro mlink(text, href, attribs) %}
    <a href="{{ href }}" {{ attribs }}><span class="link-text">{{ text }}</span> <span class="link-arrow">🡪</span></a>
{% endmacro %}""").module.mlink

template_env.globals["slink"] = template_env.from_string("""{% macro slink(text, href, attribs) %}
    <a href="{{ href }}" {{ attribs }}><span class="link-text">{{ text }}</span> <span class="link-arrow link-arrow-s">🡥</span></a>
{% endmacro %}""").module.slink

def format_datetime(value):
    # nasty hack
    if value.day in [1,21,31]:
        ordinal_suffix = "st"
    elif value.day in [2,22]:
        ordinal_suffix = "nd"
    elif value.day in [3,23]:
        ordinal_suffix = "rd"
    else:
        ordinal_suffix = "th"

    return value.strftime(f"%d<sup>{ordinal_suffix}</sup> of %B %Y")

class MathPreprocessor(InlineProcessor):
    def __init__(self, *args, dollars=None, block=False, **kwargs):
        self.dollars = dollars
        self.block = block

        super().__init__(*args,**kwargs)

    def handleMatch(self, m, data):
        # E.g. if $$ preprocessor found a $$$: reject match
        inner = m.group(1)
        if inner.startswith("$"): return None,None,None

        if self.block:
            el = etree.Element("p")
            command_line = "npx katex -d"
        else:
            el = etree.Element("span")
            command_line = "npx katex"

        proc = subprocess.Popen(
            command_line,
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            shell=True)
        
        span = m.span(1)

        if self.block:
            formula = "\displaystyle" + inner
        else:
            formula = inner
        
        proc.stdin.write(formula.encode("utf-8"))
        proc.stdin.close()

        html_content = proc.stdout.read().decode("utf-8")
        el.text = html_content

        return el, span[0]-self.dollars, span[1]+self.dollars

class MathExtension(Extension):
    def extendMarkdown(self, md):
        md.inlinePatterns.register(MathPreprocessor("\$\$(.*)\$\$", md, dollars=2), 'mathinline', 175)
        md.inlinePatterns.register(MathPreprocessor("\$\$\$(.*)\$\$\$", md, dollars=3, block=True), 'mathblock', 200)

class CustomLinkProcessor(InlineProcessor):
    def handleMatch(self, m, data):
        print("Match!", m, data)
        el = etree.Element('span')

        el.text = f'{{{{ slink("{m.group(1)}", "{m.group(2)}", "") }}}}'
        
        return el, m.span()[0], m.span()[1]
    

class CustomLinkExtension(Extension):
    def extendMarkdown(self, md: markdown.Markdown):
        md.inlinePatterns.register(CustomLinkProcessor("\[(.*)\]\((.*)\)", md), 'customlink', 300)

parser = argparse.ArgumentParser()
parser.add_argument("--no-math", action="store_true")
args = parser.parse_args()

template_env.filters["format_datetime"] = format_datetime

post_template = template_env.get_template("post.jinja.html")

def process_template(template_string, args={}):
    return template_env.from_string(template_string).render(**args)

if Path("build").exists():
    shutil.rmtree("build")

posts = []

# Read in posts
for markdown_path in glob.iglob(os.path.join("posts","**/*.md"), recursive=True):
    
    try:
        with open(markdown_path, "r", encoding="utf-8") as markdown_source:
            
            extensions = ['meta', CustomLinkExtension()]
            if not args.no_math:
                extensions.append(MathExtension())

            md = markdown.Markdown(extensions=extensions)

            converted_content = md.convert(markdown_source.read())

            html_content = process_template(converted_content)

            metadata = md.Meta

            # Validate metadata
            [post_title] = metadata["title"]
            [post_publish_date] = metadata["published"]
            [post_extract] = metadata["extract"]
            

            rel_path_md = Path(*Path(markdown_path).parts[1:])

            if IS_DEPLOYMENT: # don't need .html bit for GH pages in a link
                post_html_path = rel_path_md.with_suffix("")
            else:
                post_html_path = rel_path_md.with_suffix(".html")


            post = Post(
                title=post_title,
                url="/posts/"+str(post_html_path),
                published=dateutil.parser.parse(post_publish_date),
                extract=post_extract,
                html=html_content,
                math_enabled=True
            )

            posts.append(post)

            out_path = "build" / ("posts" / post_html_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)

            with open(out_path, "wb") as f_out:
                f_out.write(post_template.render(post=post).encode("utf-8"))

           
    except (KeyError, ValueError) as e:
        raise Exception(f"Error raised while processing post {post}") from e

context = {
    "posts": posts
}

for filename in glob.iglob(os.path.join("site", '**/*'), recursive=True):
    if filename.endswith(".jinja.html"):
        with open(filename, "rb") as f:
            rel_path = Path(*Path(filename).parts[1:])

            # bug here! hope containing directory doesn't contain .
            out_path = "build" / Path(str(rel_path).split(".")[0]).with_suffix(".html")
            out_path.parent.mkdir(parents=True, exist_ok=True)

            
            with open(out_path, "wb") as f_out:
                f_out.write(process_template(f.read().decode("utf-8"), context).encode("utf-8"))
    else:
        rel_path = Path(*Path(filename).parts[1:])
        new_path = "build" / rel_path
        shutil.copy(filename, new_path)