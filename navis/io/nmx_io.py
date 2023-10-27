#    This script is part of navis (http://www.github.com/navis-org/navis).
#    Copyright (C) 2018 Philipp Schlegel
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.

import io

import networkx as nx
import pandas as pd
import xml.etree.ElementTree as ET

from pathlib import Path


from typing import Union, Dict, Optional, Any, IO, Iterable, List
from zipfile import ZipFile

from .. import config, core
from . import base
from .swc_io import make_swc_table


__all__ = ["read_nmx", "read_nml", "write_nmx", "write_nml"]

# Set up logging
logger = config.get_logger(__name__)

NODE_COLUMNS = ('node_id', 'label', 'x', 'y', 'z', 'radius', 'parent_id')
DEFAULT_PRECISION = 32
DEFAULT_FMT = "{name}.nmx"


class NMLReader(base.BaseReader):
    def __init__(
        self,
        precision: int = DEFAULT_PRECISION,
        attrs: Optional[Dict[str, Any]] = None
    ):
        super().__init__(fmt='',
                         attrs=attrs,
                         file_ext='.nml',
                         read_binary=False,
                         name_fallback='NML')

        int_, float_ = base.parse_precision(precision)
        self._dtypes = {
            'node_id': int_,
            'parent_id': int_,
            'label': 'category',
            'x': float_,
            'y': float_,
            'z': float_,
            'radius': float_,
        }

    def read_buffer(
        self, f: IO, attrs: Optional[Dict[str, Any]] = None
    ) -> 'core.TreeNeuron':
        """Read .nml buffer into a TreeNeuron.

        NML files are XML-encoded files containing data for a single neuron.

        Parameters
        ----------
        f :         IO
                    Readable buffer (must be bytes).
        attrs :     dict | None
                    Arbitrary attributes to include in the TreeNeuron.

        Returns
        -------
        core.TreeNeuron
        """
        return self.read_nml(f.read(), attrs=attrs)

    def read_nml(
        self, f: IO, attrs: Optional[Dict[str, Any]] = None
    ) -> 'core.TreeNeuron':
        """Read .nml buffer into a TreeNeuron.

        NML files are XML files containing a single neuron.

        Parameters
        ----------
        f :         IO
                    Readable buffer.
        attrs :     dict | None
                    Arbitrary attributes to include in the TreeNeuron.

        Returns
        -------
        core.TreeNeuron
        """
        if isinstance(f, bytes):
            f = f.decode()

        f = io.StringIO(f)
        root = ET.parse(f).getroot()

        # Copy the attributes dict
        for element in root:
            if element.tag == 'thing':
                nodes = pd.DataFrame.from_records([n.attrib for n in element[0]])
                edges = pd.DataFrame.from_records([n.attrib for n in element[1]])
                edges = edges.astype(self._dtypes['node_id'])

                nodes.rename({'id': 'node_id'}, axis=1, inplace=True)
                nodes = nodes.astype({k: v for k, v in self._dtypes.items() if k in nodes.columns})

        G = nx.Graph()
        G.add_edges_from(edges.values)
        tree = nx.bfs_tree(G, list(G.nodes)[0])
        edges = pd.DataFrame(list(tree.edges), columns=['source', 'target'])
        nodes['parent_id'] = edges.set_index('target').reindex(nodes.node_id.values).source.values
        nodes['parent_id'] = nodes.parent_id.fillna(-1).astype(self._dtypes['node_id'])
        nodes.sort_values('node_id', inplace=True)

        return core.TreeNeuron(
            nodes,
            **(self._make_attributes({'name': 'NML', 'origin': 'nml'}, attrs))
        )


class NMXReader(NMLReader):
    """This is a version of the NML file reader that reads from zipped archives."""
    def __init__(
        self,
        precision: int = DEFAULT_PRECISION,
        attrs: Optional[Dict[str, Any]] = None
    ):
        super().__init__(precision=precision,
                         attrs=attrs)

        # Overwrite some of the settings
        self.read_binary = True
        self.file_ext = '.nmx'
        self.name_fallback = 'NMX'

    def read_buffer(
        self, f: IO, attrs: Optional[Dict[str, Any]] = None
    ) -> 'core.TreeNeuron':
        """Read .nmx buffer into a TreeNeuron.

        NMX files are zip files containing XML-encoded .nml files containing
        data for a single neuron.

        Parameters
        ----------
        f :         IO
                    Readable buffer (must be bytes).
        attrs :     dict | None
                    Arbitrary attributes to include in the TreeNeuron.

        Returns
        -------
        core.TreeNeuron
        """
        if not isinstance(f.read(0), bytes):
            raise ValueError(f'Expected bytes, got "{type(f.read(0))}"')

        zip = ZipFile(f)
        for f in zip.filelist:
            if f.filename.endswith('.nml') and 'skeleton' in f.filename:
                attrs['file'] = f.filename
                attrs['id'] = f.filename.split('/')[0]
                return self.read_nml(zip.read(f), attrs=attrs)
        logger.warning(f'Skipped "{f.filename.split("/")[0]}.nmx": failed to '
                       'import skeleton.')


def read_nmx(f: Union[str, pd.DataFrame, Iterable],
             include_subdirs: bool = False,
             parallel: Union[bool, int] = 'auto',
             precision: int = 32,
             limit: Optional[int] = None,
             **kwargs) -> 'core.NeuronObject':
    """Read NMX files into Neuron/Lists.

    NMX is an xml-based format used by pyKNOSSOS.
    See e.g. `here <https://doi.org/10.5281/zenodo.58985>`_ for a data dump
    of neurons from Wanner et al. (2016).

    Parameters
    ----------
    f :                 str
                        Filename or folder. If folder, will import all ``.nmx``
                        files.
    include_subdirs :   bool, optional
                        If True and ``f`` is a folder, will also search
                        subdirectories for ``.nmx`` files.
    parallel :          "auto" | bool | int
                        Defaults to ``auto`` which means only use parallel
                        processing if more than 200 files are imported. Spawning
                        and joining processes causes overhead and is
                        considerably slower for imports of small numbers of
                        neurons. Integer will be interpreted as the
                        number of cores (otherwise defaults to
                        ``os.cpu_count() // 2``).
    precision :         int [8, 16, 32, 64] | None
                        Precision for data. Defaults to 32 bit integers/floats.
                        If ``None`` will let pandas infer data types - this
                        typically leads to higher than necessary precision.
    limit :             int, optional
                        If reading from a folder you can use this parameter to
                        read only the first ``limit`` NMX files. Useful if
                        wanting to get a sample from a large library of
                        skeletons.
    **kwargs
                        Keyword arguments passed to the construction of
                        ``navis.TreeNeuron``. You can use this to e.g. set
                        meta data.

    Returns
    -------
    navis.NeuronList

    """
    reader = NMXReader(precision=precision,
                       attrs=kwargs)
    # Read neurons
    neurons = reader.read_any(f,
                              parallel=parallel,
                              limit=limit,
                              include_subdirs=include_subdirs)

    # Failed reads will produce empty neurons which we need to remove
    if isinstance(neurons, core.NeuronList):
        neurons = neurons[neurons.has_nodes]

    return neurons


def read_nml(f: Union[str, pd.DataFrame, Iterable],
             include_subdirs: bool = False,
             parallel: Union[bool, int] = 'auto',
             precision: int = 32,
             limit: Optional[int] = None,
             **kwargs) -> 'core.NeuronObject':
    """Read xml-based NML files into Neuron/Lists.

    Parameters
    ----------
    f :                 str
                        Filename or folder. If folder, will import all ``.nml``
                        files.
    include_subdirs :   bool, optional
                        If True and ``f`` is a folder, will also search
                        subdirectories for ``.nml`` files.
    parallel :          "auto" | bool | int
                        Defaults to ``auto`` which means only use parallel
                        processing if more than 200 files are imported. Spawning
                        and joining processes causes overhead and is
                        considerably slower for imports of small numbers of
                        neurons. Integer will be interpreted as the
                        number of cores (otherwise defaults to
                        ``os.cpu_count() // 2``).
    precision :         int [8, 16, 32, 64] | None
                        Precision for data. Defaults to 32 bit integers/floats.
                        If ``None`` will let pandas infer data types - this
                        typically leads to higher than necessary precision.
    limit :             int, optional
                        If reading from a folder you can use this parameter to
                        read only the first ``limit`` NML files. Useful if
                        wanting to get a sample from a large library of
                        skeletons.
    **kwargs
                        Keyword arguments passed to the construction of
                        ``navis.TreeNeuron``. You can use this to e.g. set
                        meta data.

    Returns
    -------
    navis.NeuronList

    """
    reader = NMLReader(precision=precision,
                       attrs=kwargs)
    # Read neurons
    neurons = reader.read_any(f,
                              parallel=parallel,
                              limit=limit,
                              include_subdirs=include_subdirs)

    return neurons


def write_nml(x: 'core.NeuronObject',
              filepath: Union[str, Path],
              return_node_map: bool = False) -> None:
    """Write TreeNeuron(s) to NML.

    Follows the format described
    `here <https://docs.webknossos.org/webknossos/data_formats.html>`_.

    Parameters
    ----------
    x :                 TreeNeuron | Dotprops | NeuronList
                        If multiple neurons, will generate a single SWC file
                        for each neuron (see also ``filepath``).
    filepath :          str | pathlib.Path | list thereof
                        Destination for the SWC files. See examples for options.
                        If ``x`` is multiple neurons, ``filepath`` must either
                        be a folder, a "formattable" filename, a filename ending
                        in `.zip` or a list of filenames (one for each neuron
                        in ``x``). Existing files will be overwritten!

    return_node_map :   bool
                        If True, will return a dictionary mapping the old node
                        ID to the new reindexed node IDs in the file.

    Returns
    -------
    node_map :          dict
                        Only if ``return_node_map=True``.

    See Also
    --------
    :func:`navis.read_nml`
                        Import skeleton from NML files.

    """

    root = ET.Element('things')

    # Parameters section
    parameters = ET.SubElement(root, 'parameters')
    offset = ET.SubElement(parameters, 'offset', x='0', y='0', z='0')
    scale = ET.SubElement(parameters, 'scale', x='1', y='1', z='1')

    # This neuron
    thing = ET.SubElement(root, 'thing', id="1")
    thing.attrib["name"] = x.name
    thing.attrib.update({"color.r": '0.0',
                         "color.g": '0.0',
                         "color.b": '1.0',
                         "color.a": '1.0'})
    
    nodes = ET.SubElement(thing, 'nodes')
    edges = ET.SubElement(thing, 'edges')

    # Use the SWC table as the basis for nodes & edges
    res = make_swc_table(x,
        labels=False,
        export_connectors=False,
        return_node_map=return_node_map)
    
    if return_node_map:
        swc, node_map = res[0], res[1]
    else:
        swc = res
    
    for index, row in swc.iterrows():
        node = ET.SubElement(nodes, 'node')
        node.attrib.update({"id": str(int(row["PointNo"])),
                            "radius": str(row["Radius"]),
                            "x": str(row["X"]),
                            "y": str(row["Y"]),
                            "z": str(row["Z"])})
        if row["Parent"] != -1:
            edge = ET.SubElement(edges, 'edge', source=str(int(row["Parent"])),
                                 target=str(int(row["PointNo"])))

    with open(filepath, 'wb') as file:
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ", level=0)
        tree.write(file)

    if return_node_map:
        return node_map



def write_nmx():
    """
    TODO: Generate NMX files (collection of NML files)
    """
    raise NotImplementedError("Not yet implemented")