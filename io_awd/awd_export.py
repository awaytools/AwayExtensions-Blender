import re
import os.path
import functools
import mathutils

from math import radians

import bpy

import pyawd
from pyawd.core import *
from pyawd.anim import *
from pyawd.scene import *
from pyawd.geom import *
from pyawd.material import *
from pyawd.utils.math import *
from pyawd.utils.geom import AWDGeomUtil


class AWDBlockCache(object):
    '''A cache of already created AWD blocks, and their connection to
        nodes in the Maya DAG. The cache should always be checked before
        creating a blocks, so that blocks can be reused within the file
        when possible.'''
    
    def __init__(self):
        self.__cache = []

    def get(self, path):
        block = None
        for item in self.__cache:
            if item[0] == path:
                block = item[1]
                break

        return block

    def add(self, path, block):
        if self.get(path) is None:
            self.__cache.append((path, block))
        


class AWDExporter(object):

    def __init__(self):
        self.block_cache = AWDBlockCache()
        self.exported_skeletons = []
        self.animation_sequences = []
        self.exported_objects = []
        #self.vertex_indices = {}
        
        # TODO: Don't hard code these
        self.compression = DEFLATE
        self.user_ns = AWDNamespace('default')


    def export(self, context, filepath='',
            include_materials = True,
            embed_textures = True,
            include_attr = True):

        self.context = context
        self.include_materials = include_materials
        self.embed_textures = embed_textures
        self.include_attr = include_attr
        self.awd = AWD(self.compression)

        
        for o in self.context.scene.objects:
            if o.type == 'EMPTY':
                self.export_container(o)
                
            elif o.type == 'MESH':
                self.export_mesh(o)
                
            elif o.type == 'ARMATURE':
                self.export_skeleton(o)
                
      
      
        # Loop through scene objects again and add either directly
        # to the AWD document root or to it's parent if one exists.
        # At this point, an AWD representation of the parent is
        # guaranteed to have been created if included in the export.
        for o in self.exported_objects:
            block = self.block_cache.get(o)
            if o.parent is not None:
                if o.parent.type == 'ARMATURE':
                    self.extract_joint_weights(o)
                    if o.parent.parent is not None:
                        par_block = self.block_cache.get(o.parent.parent)
                        par_block.add_child(block)
                    else:
                        self.awd.add_scene_block(block)
                else:
                    par_block = self.block_cache.get(o.parent)
                    par_block.add_child(block)
            else:
                self.awd.add_scene_block(block)
        
        
        # Export animation sequences
        # self.export_animation()
        
        
        with open(filepath, 'wb') as f:
            self.awd.flush(f)
    
    
    def extract_joint_weights(self, o):
        armature = o.parent
        geom = o.data
        skel = self.block_cache.get(armature)
                    
        # TODO: Don't hard code
        joints_per_vert = 3
        
        joint_weights = []
        joint_indices = []
        
        vert_indices = self.vertex_indices[geom.name]
        for bl_vidx in vert_indices:
            v = geom.vertices[bl_vidx]
            
            weight_objs = []
            for ge in v.groups:
                group = o.vertex_groups[ge.group]
                j_idx = skel.joint_index(name=group.name)
                if j_idx is not None:
                    weight_objs.append((j_idx, ge.weight))
                else:
                    weight_objs.append((0, 0))
            
            # Normalize weights by slicing to the desired length, calculating
            # the sum of all weights and then dividing all weights by that sum.
            weight_objs = weight_objs[0:joints_per_vert]
            sum_obj = functools.reduce(lambda w0,w1: (0, w0[1]+w1[1]), weight_objs)
            weight_objs = [(w[0], w[1]/sum_obj[1]) for w in weight_objs]
            
            # Add more empty weight objects if too few
            if len(weight_objs) != joints_per_vert:
                weight_objs.extend([(0,0)] * (joints_per_vert-len(weight_objs)))
            
            for w_obj in weight_objs:
                joint_indices.append(w_obj[0])
                joint_weights.append(w_obj[1])
            
        
        # Add newly assembled streams
        md = self.block_cache.get(geom)
        md[0].add_stream(STR_JOINT_WEIGHTS, joint_weights)
        md[0].add_stream(STR_JOINT_INDICES, joint_indices)
            

        
    
    def export_container(self, o):
        mtx = self.mtx_bl2awd(o.matrix_local)
        ctr = AWDContainer(name=o.name, transform=mtx)
        self.block_cache.add(o, ctr)
        self.exported_objects.append(o)
        
        if self.include_attr:
            self.set_attributes(o, ctr)
        
        
        
    def export_animation(self):
        # Unlock from bind pose
        for o in self.exported_skeletons:
            o.data.pose_position = 'POSE'
            
        for seq in self.animation_sequences:
            skel_anims = {}
            for o in self.exported_skeletons:
                skel_anim = AWDSkeletonAnimation(seq[0])
                skel_anims[o.name] = skel_anim
                self.awd.add_skeleton_anim(skel_anim)
            
            print('Exporting sequences %s (%d-%d)' % seq)
            
            for frame in range(seq[1], seq[2]):
                self.context.scene.frame_set(frame)
                for o in self.exported_skeletons:
                    skel_pose = AWDSkeletonPose()
                    
                    for bp in o.pose.bones:
                        mtx = self.mtx_bl2awd(bp.matrix_basis)
                        skel_pose.add_joint_transform(mtx)
                    

                    # Pad with an identity transform to match the number
                    # of joints (for first joint both head and tail were
                    # included when skeleton was created.)
                    skel_pose.add_joint_transform(
                        self.mtx_bl2awd(mathutils.Matrix()))
                    
                    self.awd.add_skeleton_pose(skel_pose)
                    skel_anims[o.name].add_frame(skel_pose, 40)
                    
            
                
                
    
    
    def export_mesh(self, o):
        md = self.block_cache.get(o.data)
        if md is None:
            print('Creating mesh %s' % o.data.name)
            # If bound to a skeleton, set that skeleton in bind pose
            # to make sure that the geometry is defined in that state
            if o.parent is not None and o.parent.type == 'ARMATURE':
                o.parent.data.pose_position = 'REST'
                
            md = self.build_mesh_data(o.data)
            self.awd.add_mesh_data(md)
            self.block_cache.add(o.data, md)
        
        mtx = self.mtx_bl2awd(o.matrix_local)
        inst = AWDMeshInst(data=md, name=o.name, transform=mtx)
        self.block_cache.add(o, inst)
        
        if self.include_materials:
            print('Checking materials for %s' % o.name)
            awd_mat = None
            for ms in o.material_slots:
                awd_tex = None
                bl_mat = ms.material

                if bl_mat is None or bl_mat.type != 'SURFACE':
                    continue # Ignore non-surface materials for now
                
                awd_mat = self.block_cache.get(bl_mat)
                
                if awd_mat is None:
                    for ts in bl_mat.texture_slots:
                        # Skip empty slots
                        if ts is None:
                            continue
                        
                        if ts.use_map_color_diffuse:
                            # Main input!
                            bl_tex = ts.texture
                            
                            awd_tex = self.block_cache.get(bl_tex)
                            
                            if awd_tex is None:
                                if bl_tex.type == 'IMAGE' and bl_tex.image is not None:
                                    bl_img = bl_tex.image
                                    # BitmapMaterial
                                    if self.embed_textures:
                                        tex_type = None
                                        if bl_img.file_format == 'PNG':
                                            tex_type = AWDTexture.EMBED_PNG
                                        elif bl_img.file_format == 'JPG':
                                            tex_type = AWDTexture.EMBED_JPG
                                            
                                        awd_tex = AWDTexture(tex_type, name=bl_tex.name)
                                        awd_tex.embed_file( bpy.path.abspath( bl_img.filepath ))
                                    else:
                                        awd_tex = AWDTexture(AWDTexture.EXTERNAL, name=bl_tex.name)
                                        awd_tex.url = bl_img.filepath
                                 
                                    self.block_cache.add(bl_tex, awd_tex)
                                    self.awd.add_texture(awd_tex)
                                    
                                    break
                            
                    print('Found texture to create material?')
                    if awd_tex is not None:
                        awd_mat = AWDMaterial(AWDMaterial.BITMAP, name=bl_mat.name)
                        awd_mat.texture = awd_tex
                        
                        if self.include_attr:
                            self.set_attributes(bl_mat, awd_mat)
                        
                        print('Yes! Created material!')
                        self.block_cache.add(bl_mat, awd_mat)
                        self.awd.add_material(awd_mat)
            
            
            if awd_mat is not None:
                inst.materials.append(awd_mat)
                
                
        
        if self.include_attr:
            self.set_attributes(o, inst)
        
        self.exported_objects.append(o)    
    
    def export_skeleton(self, o):
        root_joint = None
        
        # Use bind pose
        o.data.pose_position = 'REST'
        
        for b in o.data.bones:
            joint = AWDSkeletonJoint(b.name)
            joint.inv_bind_mtx = self.mtx_bl2awd(
                mathutils.Matrix.Translation(b.tail_local).inverted())
                
            if root_joint is None:
                root_joint = AWDSkeletonJoint('root')
                root_joint.add_child_joint(joint)
                root_joint.inv_bind_mtx = self.mtx_bl2awd(
                    mathutils.Matrix.Translation(b.head_local).inverted())
            else:
                p_block = self.block_cache.get(b.parent)
                if p_block is not None:
                    p_block.add_child_joint(joint)
            
            self.block_cache.add(b, joint)
        
        if root_joint is not None:
            skel = AWDSkeleton(name=o.name)
            skel.root_joint = root_joint
            self.awd.add_skeleton(skel)
            self.block_cache.add(o, skel)
            self.exported_skeletons.append(o)
    
    def build_mesh_data(self, geom):
        vertex_edges = {}

        geom_util = AWDGeomUtil()
        
        # Create lookup table for edges by vertex, to use
        # when determining if a vertex is on a hard edge
        for e in geom.edges:
            for v in e.vertices:
                if v not in vertex_edges:
                    vertex_edges[v] = []
                vertex_edges[v].append(e)
        
        tex_data = None
        has_uvs = False
        if len(geom.uv_textures):
            has_uvs = True
            tex_data = geom.uv_textures[0].data
        
        # Generate expanded list of vertices
        for f in geom.faces:
            inds_in_face = [0,2,1]
            if len(f.vertices)==4:
                inds_in_face.extend((0,3,2))
                
            for idx in inds_in_face:
                vert = geom.vertices[f.vertices[idx]]
                edges = vertex_edges[vert.index]
                has_hard_edge = False
                for e in edges:
                    if e.use_edge_sharp:
                        has_hard_edge = True
                        break

                uv = None
                if tex_data is not None and len(tex_data)>0:
                    # TODO: Implement secondary UV sets?
                    tex_face = tex_data[f.index]
                    uv = [tex_face.uv[idx][0], 1.0-tex_face.uv[idx][1]]

                v = [vert.co.x, vert.co.z, vert.co.y]
                n = [f.normal.x, f.normal.z, f.normal.y] 
                geom_util.append_vert_data(vert.index, v, uv, n, has_hard_edge)


        # Find influences for all vertices
        #for v0 in expanded_vertices:
        #   for v1 in expanded_vertices:
        #       angle = degrees(v0['normal'].angle(v1['normal']))
        #       if angle <= geom.auto_smooth_angle:
        #           v0['normal_influences'].append(v1['f'])
        #           v1['normal_influences'].append(v0['f'])

        
        md = AWDMeshData(geom.name)
        if geom.use_auto_smooth:
            geom_util.normal_threshold = geom.auto_smooth_angle
        geom_util.build_geom(md)

        #md.add_sub_mesh(AWDSubMesh())
        #md[0].add_stream(STR_VERTICES, vertices)
        #md[0].add_stream(STR_TRIANGLES, indices)
        #md[0].add_stream(STR_VERTEX_NORMALS, normals)
        
        return md
    
    
    def set_attributes(self, ob, awd_elem):
        for key in ob.keys():
            if (key != '_RNA_UI'):
                print('setting prop %s.%s=%s' % (ob.name, key, ob[key]))
                awd_elem.attributes[self.user_ns][str(key)] = str(ob[key])
                    
    def mtx_bl2awd(self, mtx):    
        # Decompose matrix
        pos, rot, scale = mtx.decompose()
        
        # Swap translation axes
        tmp = pos.y
        pos.y = pos.z
        pos.z = tmp
        
        # Swap rotation axes
        tmp = rot.y
        rot.x = -rot.x
        rot.y = -rot.z
        rot.z = -tmp
        
        # Recompose matrix
        mtx = mathutils.Matrix.Translation(pos).to_4x4() * rot.to_matrix().to_4x4()
        
        # Create list from rows
        rows = list(mtx)
        mtx_list = []
        mtx_list.extend(list(rows[0]))
        mtx_list.extend(list(rows[1]))
        mtx_list.extend(list(rows[2]))
        mtx_list.extend(list(rows[3]))
        
        # Apply swapped-axis scale
        mtx_list[0] *= scale.x
        mtx_list[5] *= scale.y
        mtx_list[10] *= scale.z
        
        #print(mtx_list[0:4])
        #print(mtx_list[4:8])
        #print(mtx_list[8:12])
        #print(mtx_list[12:])
        
        return AWDMatrix4x4(mtx_list)
    



if __name__ == '__main__':
    def read_sequences(seq_path, base_path):
        sequences = []
        if seq_path is not None:
            if not os.path.isabs(seq_path):
                # Look for this file in a list of different locations,
                # and use the first one in which it exists.
                existed = False
                bases = [
                    bpy.path.abspath('//'),
                    base_path
                ]

                for base in bases:
                    new_path = os.path.join(base, seq_path)
                    print('Looking for sequence file in %s' % new_path)
                    if os.path.exists(new_path) and os.path.isfile(new_path):
                        existed = True
                        seq_path = new_path
                        break

                if not existed:
                    #mc.warning('Could not find sequence file "%s. Will not export animation."' % seq_path)
                    return []

            try:
                with open(seq_path, 'r') as seqf:
                    lines = seqf.readlines()
                    for line in lines:
                        # Skip comments
                        if line[0] == '#':
                            continue

                        line_fields = re.split('[^a-zA-Z0-9]', line.strip())
                        sequences.append((line_fields[0], int(line_fields[1]), int(line_fields[2])))
            except:
                raise
                pass

        return sequences

    exporter = BlenderAWDExporter(bpy.path.abspath('//blendout.awd'))
    exporter.animation_sequences = read_sequences('sequences.txt', '.')
    exporter.export()
