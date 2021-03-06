import re
import tarfile  # type: ignore
import lxml.etree  # type: ignore
from typing import Dict, Any, Tuple

from nextcloudappstore.core.api.v1.release import ReleaseConfig
from nextcloudappstore.core.versioning import pad_max_version, pad_min_version
from rest_framework.exceptions import APIException  # type: ignore


class MaxSizeAppMetadataXmlException(APIException):
    pass


class InvalidAppMetadataXmlException(APIException):
    pass


class UnsupportedAppArchiveException(APIException):
    pass


class InvalidAppPackageStructureException(APIException):
    pass


class XMLSyntaxError(APIException):
    pass


class GunZipAppMetadataExtractor:
    def __init__(self, config: ReleaseConfig) -> None:
        """
        :argument config the config
        """
        self.config = config
        self.app_folder_regex = re.compile(r'^[a-z]+[a-z_]*$')

    def extract_app_metadata(self, archive_path: str) -> Tuple[str, str]:
        """
        Extracts the info.xml from an tar.gz archive
        :argument archive_path the path to the tar.gz archive
        :raises InvalidAppPackageStructureException if the first level folder
        does not equal the app_id or no info.xml file could be found in the
        appinfo folder
        :return the info.xml as string
        """
        if not tarfile.is_tarfile(archive_path):
            msg = '%s is not a valid tar.gz archive ' % archive_path
            raise UnsupportedAppArchiveException(msg)

        with tarfile.open(archive_path, 'r:gz') as tar:
            result = self._parse_archive(tar)
        return result

    def _parse_archive(self, tar: Any) -> Tuple[str, str]:
        folder = list(
            filter(lambda name: re.match(self.app_folder_regex, name),
                   tar.getnames()
                   )
        )
        if len(folder) > 1:
            msg = 'More than one possible app folder found'
            raise InvalidAppPackageStructureException(msg)
        elif len(folder) == 0:
            msg = 'No possible app folder found. App folder must contain ' \
                  'only lowercase ASCII characters or underscores'
            raise InvalidAppPackageStructureException(msg)

        app_id = folder[0]
        info_path = '%s/appinfo/info.xml' % app_id
        try:
            app_member = tar.getmember(app_id)
            appinfo_member = tar.getmember('%s/appinfo' % app_id)
            info_member = tar.getmember(info_path)
            possible_links = [app_member, appinfo_member, info_member]

            for possible_link in possible_links:
                if possible_link.issym() or possible_link.islnk():
                    msg = 'Symlinks and hard links can not be used for %s' %\
                          possible_link
                    raise InvalidAppPackageStructureException(msg)

            if info_member.size > self.config.max_info_size:
                msg = '%s was bigger than allowed %i bytes' % (
                    info_path, self.config.max_info_size)
                raise MaxSizeAppMetadataXmlException(msg)
            info_file = tar.extractfile(info_member)
            return info_file.read().decode('utf-8'), app_id
        except KeyError:
            msg = 'Could not find %s file inside the archive' % info_path
            raise InvalidAppPackageStructureException(msg)


def element_to_dict(element: Any) -> Dict:
    type = element.get('type')
    key = element.tag.replace('-', '_')
    if type == 'int':
        return {key: int(element.text)}
    elif type == 'list':
        return {key: list(map(element_to_dict, element.iterchildren()))}
    elif type == 'min-version':
        return {key: pad_min_version(element.text)}
    elif type == 'max-version':
        return {key: pad_max_version(element.text)}
    elif len(list(element)) > 0:
        contents = {}
        for child in element.iterchildren():
            contents.update(element_to_dict(child))
        return {key: contents}
    else:
        return {key: element.text}


def parse_app_metadata(xml: str, schema: str, pre_xslt: str,
                       xslt: str) -> Dict:
    """
    Parses, validates and maps the xml onto a dict
    :argument xml the info.xml string to parse
    :argument schema the schema xml as string
    :argument pre_xslt xslt which is run before validation to ensure that
    everything is in the correct order and that unknown elements are excluded
    :argument xslt the xslt to transform it to a matching structure
    :raises InvalidAppMetadataXmlException if the schema does not validate
    :return the parsed xml as dict
    """
    parser = lxml.etree.XMLParser(resolve_entities=False, no_network=True,
                                  remove_comments=True, load_dtd=False,
                                  remove_blank_text=True, dtd_validation=False
                                  )
    try:
        doc = lxml.etree.fromstring(bytes(xml, encoding='utf-8'), parser)
    except lxml.etree.XMLSyntaxError as e:
        msg = 'info.xml contains malformed xml: %s' % e
        raise XMLSyntaxError(msg)
    for _ in doc.iter(lxml.etree.Entity):
        raise InvalidAppMetadataXmlException('Must not contain entities')
    pre_transform = lxml.etree.XSLT(lxml.etree.XML(pre_xslt))
    pre_transformed_doc = pre_transform(doc)
    schema_doc = lxml.etree.fromstring(bytes(schema, encoding='utf-8'), parser)
    schema = lxml.etree.XMLSchema(schema_doc)
    try:
        schema.assertValid(pre_transformed_doc)  # type: ignore
    except lxml.etree.DocumentInvalid as e:
        msg = 'info.xml did not validate: %s' % e
        raise InvalidAppMetadataXmlException(msg)
    transform = lxml.etree.XSLT(lxml.etree.XML(xslt))
    transformed_doc = transform(pre_transformed_doc)
    mapped = element_to_dict(transformed_doc.getroot())
    return mapped
