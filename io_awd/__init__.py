import bpy
import bpy.path
from bpy.props import *
from bpy_extras.io_utils import ExportHelper



__all__ = ['awd_export']


bl_info = {
    'name': 'Away3D AWD2 format',
    'author': 'Richard Olsson',
    'blender': (2,5,9),
    'api': 35622,
    'location': 'File > Export',
    'description': 'Export AWD2 files',
    'warning': '',
    'category': 'Import-Export'
}



class ExportAWD(bpy.types.Operator, ExportHelper):
    bl_idname = 'away3d.awd_export'
    bl_label = 'Export AWD'
    bl_options = {'PRESET'}

    filename_ext = '.awd'
    filter_glob = StringProperty(
        default = '*.awd',
        options = {'HIDDEN'})

    include_materials = BoolProperty(
        name = 'Include materials',
        default = True)

    embed_textures = BoolProperty(
        name = 'Embed textures',
        default = True)

    include_attr = BoolProperty(
        name = 'Include attributes',
        description = 'Export Blender custom properties as AWD user attributes',
        default = True)


    def draw(self, context):
        layout = self.layout

        layout.prop(self, 'include_materials')
        layout.prop(self, 'embed_textures')
        layout.prop(self, 'include_attr')
    

    def execute(self, context):
        from . import awd_export
        kwds = self.as_keywords(ignore=('check_existing','filter_glob'))
        print(kwds)

        exporter = awd_export.AWDExporter()
        exporter.export(context, **kwds)
        return {'FINISHED'}


def menu_func_export(self, context):
    self.layout.operator(ExportAWD.bl_idname, text="Away3D (.awd)")


def register():
    bpy.utils.register_module(__name__)
    bpy.types.INFO_MT_file_export.append(menu_func_export)

def unregister():
    bpy.utils.unregister_module(__name__)
    bpy.types.INFO_MT_file_export.remove(menu_func_export)


