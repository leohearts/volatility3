import logging
import os
import re
import typing

from volatility.framework import interfaces, renderers
from volatility.framework.configuration import requirements
from volatility.framework.layers import intel
from volatility.framework.renderers import format_hints
from volatility.plugins.windows import pslist

vollog = logging.getLogger(__name__)


class Strings(interfaces.plugins.PluginInterface):

    @classmethod
    def get_requirements(cls):
        return [requirements.TranslationLayerRequirement(name = 'primary',
                                                         description = 'Kernel Address Space',
                                                         architectures = ["Intel32", "Intel64"]),
                requirements.SymbolRequirement(name = "nt_symbols", description = "Windows OS"),
                requirements.StringRequirement(name = "strings_file", description = "Strings file")]
        # TODO: Make URLRequirement that can accept a file address which the framework can open

    def run(self):
        if not os.path.exists(self.config['strings_file']):
            vollog.error("File {} does not exist".format(self.config['strings_file']))

        return renderers.TreeGrid([("String", str),
                                   ("Physical Address", format_hints.Hex),
                                   ("Result", str)],
                                  self._generator())

    def _generator(self) -> typing.Generator[typing.Tuple, None, None]:
        """Generates results from a strings file"""
        revmap = self.generate_mapping(self.config['primary'])

        for line in open(self.config['strings_file'], "rb").readlines():
            try:
                offset, string = self._parse_line(line)
                try:
                    revmap_list = [name + ":" + hex(offset) for (name, offset) in revmap[offset >> 12]]
                except:
                    revmap_list = ["FREE MEMORY"]
                yield (0, (str(string, 'latin-1'), format_hints.Hex(offset), ", ".join(revmap_list)))
            except ValueError:
                vollog.error("Strings file is in the wrong format")
                raise StopIteration

    def _parse_line(self, line: bytes) -> typing.Tuple[int, bytes]:
        """Parses a single line from a strings file"""
        pattern = re.compile(rb"(?:\W*)([0-9]+)(?:\W*)(\w+)")
        match = pattern.search(line)
        offset, string = match.group(1, 2)
        return int(offset), string

    def generate_mapping(self, layer_name: str) -> typing.Dict[int, typing.List]:
        """Creates a reverse mapping between virtual addresses and physical addresses"""
        layer = self._context.memory[layer_name]
        reverse_map = dict()
        if isinstance(layer, intel.Intel):
            # We don't care about errors, we just wanted chunks that map correctly
            for mapval in layer.mapping(0x0, layer.maximum_address, ignore_errors = True):
                vpage, kpage, page_size, maplayer = mapval
                for val in range(kpage, kpage + page_size, 0x1000):
                    cur_set = reverse_map.get(kpage >> 12, set())
                    cur_set.add(("kernel", vpage))
                    reverse_map[kpage >> 12] = cur_set
                self._progress_callback((vpage * 100) / layer.maximum_address, "Creating reverse kernel map")

            # TODO: Include kernel modules

            plugin = pslist.PsList(self.context, self.config_path)

            for process in plugin.list_processes():
                proc_layer_name = process.add_process_layer()
                proc_layer = self.context.memory[proc_layer_name]
                for mapval in proc_layer.mapping(0x0, proc_layer.maximum_address, ignore_errors = True):
                    kpage, vpage, page_size, maplayer = mapval
                    for val in range(kpage, kpage + page_size, 0x1000):
                        cur_set = reverse_map.get(kpage >> 12, set())
                        cur_set.add(("Process {}".format(process.UniqueProcessId), vpage))
                        reverse_map[kpage >> 12] = cur_set
                    # FIXME: make the progress for all processes, rather than per-process
                    self._progress_callback((vpage * 100) / layer.maximum_address,
                                            "Creating mapping for task {}".format(process.UniqueProcessId))

        return reverse_map
