import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from alto import parse_alto

xml = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Description>
    <MeasurementUnit>pixel</MeasurementUnit>
    <sourceImageInformation><fileName>../samples/page_01.jpg</fileName></sourceImageInformation>
  </Description>
  <Layout>
    <Page WIDTH="1500" HEIGHT="2000">
      <PrintSpace HPOS="0" VPOS="0" WIDTH="1500" HEIGHT="2000">
        <TextBlock>
          <TextLine ID="P1">
            <Shape><Polygon POINTS="100,100 900,104 900,140 100,136"/></Shape>
            <String CONTENT="abc"/>
            <String CONTENT="def"/>
          </TextLine>
        </TextBlock>
      </PrintSpace>
    </Page>
  </Layout>
</alto>
"""

path = Path(__file__).resolve().parent.parent / "workspace/.cache/polyonly.xml"
path.write_text(xml, encoding="utf-8")
page = parse_alto(path)
line = page.lines[0]
print(line.id, line.box, line.polygon, repr(line.gt))
path.unlink()
