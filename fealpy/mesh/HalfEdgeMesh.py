import numpy as np
from scipy.sparse import coo_matrix, csc_matrix, csr_matrix, spdiags, eye, tril, triu
from ..quadrature import TriangleQuadrature, QuadrangleQuadrature, GaussLegendreQuadrature 
from .Mesh2d import Mesh2d
from .adaptive_tools import mark
from .mesh_tools import show_halfedge_mesh
from ..common.Tools import hash2map
# fixednode: 节点是否固定标记, 在网格生成与自适应算法中不能移除
# True: 固定
# False: 自由

# subdomain: 单元所处的子区域的标记编号
#  0: 表示外部无界区域
# -n: n >= 1, 表示编号为 -n 洞
#  n: n >= 1, 表示编号为  n 的内部子区域

class HalfEdgeMesh(Mesh2d):
    def __init__(self, node, subdomain, halfedge,
        NV=None, nodedof=None):
        """
        Parameters
        ----------
        node : (NN, GD)
        halfedge : (2*NE, 6), 
            halfedge[i, 0]: the index of the vertex the i-th halfedge point to
            halfedge[i, 1]: the index of the cell the i-th halfedge blong to
            halfedge[i, 2]: the index of the next halfedge of th i-th halfedge 
            halfedge[i, 3]: the index of the previous halfedge of the i-th halfedge
            halfedge[i, 4]: the index of the opposit halfedge of the i-th halfedge
            halfedge[i, 5]: the main halfedge flag, 1: main halfedge; 0: non main halfedge
        subdomain : (NC, ) the sub domain flag of each cell blong to
        """

        self.itype = halfedge.dtype
        self.ftype = node.dtype

        self.node = node
        self.ds = HalfEdgeMesh2dDataStructure(node.shape[0], 
                subdomain, halfedge, NV=NV)
        self.meshtype = 'halfedge'

        self.halfedgedata = {}
        self.celldata = {}
        self.nodedata = {}
        self.edgedata = {}
        self.facedata = self.edgedata
        self.meshdata = {}

        # 网格节点的自由度标记数组
        # 0: 固定点
        # 1: 边界上的点
        # 2: 区域内部的点
        self.nodedata['dof'] = nodedof

        self.init_level_info()


    def init_level_info(self):
        NN = self.number_of_nodes()
        NE = self.number_of_edges()

        NC = self.number_of_all_cells() # 实际单元个数
        self.celldata['level'] = np.zeros(NC, dtype=self.itype)
        self.halfedgedata['level'] = np.zeros(2*NE, dtype=self.itype)
        self.nodedata['level'] = np.zeros(NN, dtype=self.itype)

    def number_of_all_cells(self):
        return self.ds.number_of_all_cells()

    def set_data(self, name, val, etype):
        if etype in {'cell', 2}:
            NC = self.number_of_all_cells() 
            shape = (NC, ) + val.shape[1:]
            self.celldata[name] = np.zeros(shape, dtype=val.dtype) 
            self.celldata[name][self.ds.cellstart:] = val
        elif etype in {'face', 'edge', 1}:
            self.edgedata[name] = val
        elif etype in {'node', 0}:
            self.nodedata[name] = val
        elif etype == 'mesh':
            self.meshdata[name] = val
        elif etype == 'halfedge':
            self.halfedgedata[name] = val
        else:
            raise ValueError("`etype` is wrong!")

    def get_data(self, etype, name):
        if etype in {'cell', 2}:
            return self.celldata[name][self.ds.cellstart:]
        elif etype in {'face', 'edge', 1}:
            return self.edgedata[name]
        elif etype in {'node', 0}:
            return self.nodedata[name]
        elif etype == 'mesh':
            return self.meshdata[name]
        elif etype == 'halfedge':
            return self.halfedgedata[name]
        else:
            raise ValueError("`etype` is wrong!")

    def integrator(self, k, etype='cell'):
        if etype in {'cell', 'tri',  2}:
            return TriangleQuadrature(k)
        elif etype in {'quad'}:
            return QuadrangleQuadrature(k)
        elif etype in {'edge', 'face', 1}:
            return GaussLegendreQuadrature(k)

    @classmethod
    def from_mesh(cls, mesh):
        NE = mesh.number_of_edges()
        NC = mesh.number_of_cells()

        node = mesh.entity('node')
        edge = mesh.entity('edge')
        edge2cell = mesh.ds.edge_to_cell()
        isInEdge = edge2cell[:, 0] != edge2cell[:, 1]
        

        halfedge = np.zeros((2*NE, 6), dtype=mesh.itype)
        halfedge[:, 0] = edge.flat

        halfedge[0::2, 1][isInEdge] = edge2cell[isInEdge, 1] + 1
        halfedge[1::2, 1] = edge2cell[:, 0] + 1

        halfedge[0::2, 4] = range(1, 2*NE, 2)
        halfedge[1::2, 4] = range(0, 2*NE, 2)
        halfedge[1::2, 5]  = 1

        NHE = len(halfedge)
        edge = np.zeros((2*NHE, 2), dtype=halfedge.dtype)
        edge[:NHE] = halfedge[:, 0:2]
        edge[NHE:, 0] = halfedge[halfedge[:, 4], 0]
        edge[NHE:, 1] = halfedge[:, 1]
        idx = np.lexsort((edge[:, 0], edge[:, 1])).reshape(-1, 2)
        idx[:, 1] -= NHE
        halfedge[idx[:, 0], 2] = idx[:, 1]
        halfedge[halfedge[:, 2], 3] = range(NHE)

        subdomain = np.ones(NC+1, dtype=halfedge.dtype)
        subdomain[0] = 0

        return cls(node, subdomain, halfedge)

    def entity(self, etype=2):
        if etype in {'cell', 2}:
            return self.ds.cell_to_node()
        elif etype in {'edge', 'face', 1}:
            return self.ds.edge_to_node()
        elif etype in {'halfedge'}:
            return self.ds.halfedge
        elif etype in {'node', 0}:
            return self.node
        else:
            raise ValueError("`entitytype` is wrong!")

    def entity_barycenter(self, etype='cell', index=None):
        node = self.node
        dim = self.geo_dimension()
        if etype in {'cell', 2}:
            cell2node = self.ds.cell_to_node(return_sparse=True)
            NV = self.ds.number_of_vertices_of_cells().reshape(-1,1)
            bc = cell2node*node/NV
        elif etype in {'edge', 'face', 1}:
            edge = self.ds.edge_to_node()
            bc = np.sum(node[edge, :], axis=1).reshape(-1, dim)/edge.shape[1]
        elif etype in {'node', 1}:
            bc = node
        return bc

    def node_normal(self):
        node = self.node
        cell, cellLocation = self.entity('cell') # TODO: for tri and quad case
        idx1 = np.zeros(cell.shape[0], dtype=np.int)
        idx2 = np.zeros(cell.shape[0], dtype=np.int)

        idx1[0:-1] = cell[1:]
        idx1[cellLocation[1:]-1] = cell[cellLocation[:-1]]
        idx2[1:] = cell[0:-1]
        idx2[cellLocation[:-1]] = cell[cellLocation[1:]-1]
        w = np.array([(0,-1),(1,0)])
        d = node[idx1] - node[idx2]
        return 0.5*d@w

    def cell_area(self, index=None):
        NC = self.number_of_cells()
        node = self.entity('node')
        halfedge = self.ds.halfedge
        hflag = self.ds.hflag
        cidxmap = self.ds.cidxmap

        e0 = halfedge[halfedge[hflag, 3], 0]
        e1 = halfedge[hflag, 0]

        w = np.array([[0, -1], [1, 0]], dtype=np.int)
        v= (node[e1] - node[e0])@w
        val = np.sum(v*node[e0], axis=1)

        a = np.zeros(NC, dtype=self.ftype)
        np.add.at(a, cidxmap[halfedge[hflag, 1]], val)
        a /=2
        return a

    def cell_barycenter(self, return_all=False):
        GD = self.geo_dimension()
        node = self.entity('node')
        halfedge = self.ds.halfedge
        if return_all:
            NC = self.number_of_all_cells()
            e0 = halfedge[halfedge[:, 3], 0]
            e1 = halfedge[:, 0]
            w = np.array([[0, -1], [1, 0]], dtype=np.int)
            v= (node[e1] - node[e0])@w
            val = np.sum(v*node[e0], axis=1)
            ec = val.reshape(-1, 1)*(node[e1]+node[e0])/2

            a = np.zeros(NC, dtype=self.ftype)
            c = np.zeros((NC, GD), dtype=self.ftype)
            np.add.at(a, halfedge[:, 1], val)
            np.add.at(c, (halfedge[:, 1], np.s_[:]), ec)
            a /=2
            c /=3*a.reshape(-1, 1)
            return c
        else:
            NC = self.number_of_cells()
            hflag = self.ds.hflag
            cidxmap = self.ds.cidxmap
            e0 = halfedge[halfedge[hflag, 3], 0]
            e1 = halfedge[hflag, 0]
            w = np.array([[0, -1], [1, 0]], dtype=np.int)
            v= (node[e1] - node[e0])@w
            val = np.sum(v*node[e0], axis=1)
            ec = val.reshape(-1, 1)*(node[e1]+node[e0])/2
            a = np.zeros(NC, dtype=self.ftype)
            c = np.zeros((NC, GD), dtype=self.ftype)
            np.add.at(a, cidxmap[halfedge[hflag, 1]], val)
            np.add.at(c, (cidxmap[halfedge[hflag, 1]], np.s_[:]), ec)
            a /=2
            c /=3*a.reshape(-1, 1)
            return c

    def edge_bc_to_point(self, bcs, index=None):
        node = self.entity('node')
        edge = self.entity('edge')
        index = index if index is not None else np.s_[:]
        ps = np.einsum('ij, kjm->ikm', bcs, node[edge[index]])
        return ps

    def refine_tri(self, isMarkedCell):
        """
        这里假设所有的单元都是三角形, 标记的单元一分为 4
        如果有一个单元有两个边被标记, 则剩下的一个边也需要被标记
        """

        NN = self.number_of_nodes()
        NE = self.number_of_edges()
        NC = self.number_of_cells()
        
        # 单元和半边的层标记信息
        clevel = self.celldata['level']
        nlevel = self.nodedata['level']

        halfedge = self.ds.halfedge
        isMainHEdge = (halfedge[:, 5] == 1) # 主半边标记

        # 标记出二分的半边

        isBHEdge = (clevel[halfedge[:, 1]] == nlevel[halfedge[:, 0]])
        isBHEdge = isBHEdge & (nlevel[halfedge[:, 0]]  > nlevel[halfedge[halfedge[:, 2], 0]])
        isBHEdge = isBHEdge & (nlevel[halfedge[:, 0]]  > nlevel[halfedge[halfedge[:, 3], 0]])
        isBHEdge = isBHEdge & (nlevel[halfedge[:, 0]] == clevel[halfedge[halfedge[:, 4], 1]])

        """
        halfedge[halfedge[isBHEdge, 4], 1] = halfedge[isBHEdge, 1]
        nex = halfedge[halfedge[isBHEdge, 4], 2]
        pre = halfedge[halfedge[isBHEdge, 4], 3]
        halfedge[nex, 1] = halfedge[isBHEdge, 1]
        halfedge[pre, 1] = halfedge[isBHEdge, 1]
        halfedge[halfedge[isBHEdge, 2], 3] = pre
        halfedge[halfedge[isBHEdge, 3], 2] = nex
        """

        # 标记出需要加密的半边
        isMarkedHEdge = isMarkedCell[halfedge[:, 1]] & (~isBHEdge) 
        flag = ~isMarkedHEdge & isMarkedHEdge[halfedge[:, 4]]
        isMarkedHEdge[flag] = True

        N = isMarkedHEdge.sum()

        node = self.entity('node')
        flag0 = isMainHEdge & isMarkedHEdge
        idx = halfedge[flag0, 4]
        ec = (node[halfedge[flag0, 0]] + node[halfedge[idx, 0]])/2
        NE1 = len(ec)

    def coarsen_tri(self, isMarkedCell):
        pass

    def refine_quad(self, isMarkedCell):
        NC = self.number_of_all_cells()
        NN = self.number_of_nodes()
        NE = self.number_of_edges()
        NE*=2
        subdomain = self.ds.subdomain
        halfedge = self.ds.halfedge
        node = self.node
        markedge = np.zeros(NE)

        nlevel = self.nodedata['level']
        clevel = self.celldata['level']
        marked = isMarkedCell.astype(bool)
        marked[0] = False

        #蓝色半边
        tmp0 = halfedge[halfedge[halfedge[:, 4], 3], 4]
        tmp1 = halfedge[halfedge[tmp0, 3], 4]
        flag0 = nlevel[halfedge[:, 0]] > nlevel[halfedge[halfedge[:, 4], 0]]
        flag1 = nlevel[halfedge[:, 0]] == nlevel[halfedge[halfedge[:, 2], 0]]
        flag2 = nlevel[halfedge[:, 0]] == nlevel[halfedge[tmp0, 0]]
        flag3 = nlevel[halfedge[:, 0]] == nlevel[halfedge[tmp1, 0]]
        flag = flag0 & flag1 & flag2 & flag3
        isBlueHEdge = halfedge[flag, 4]

        #蓝色单元
        isBlueCell = np.zeros(NC, dtype = bool)
        isBlueCell[halfedge[isBlueHEdge, 1]] = True
        isBlueCell[halfedge[halfedge[isBlueHEdge, 4], 1]] = True
        isBlueCell[halfedge[halfedge[halfedge[isBlueHEdge, 3], 4], 1]] = True

        #标记被加密的半边
        flag[halfedge[flag,4]] = True
        tmp = marked[halfedge[:,1]] & ~flag
        markedge[tmp] = 1
        markedge[halfedge[tmp, 4]] = 1

        #RB加密策略
        haR = np.array([[0,0,0,0],
                        [1,1,1,1],
                        [1,1,0,0],
                        [0,0,1,1]])
        mapR, valR = hash2map(np.arange(16), haR)#mapR行数对应单元标记情况, 如单元[1 1 0 0]是3, 对应mapR第3行

        haB = np.array([[0, 0, 0, 0, 0, 0],
                        [1, 1, 0, 0, 0, 0],
                        [1, 1, 1, 0, 0, 1],
                        [1, 1, 0, 1, 1, 0],
                        [1, 1, 1, 1, 1, 1]], dtype = bool)
        mapB, valB = hash2map(np.arange(64), haB)

        #起始边
        cell2hedge = self.ds.cell2hedge
        cell2hedgetest = cell2hedge.copy()

        redge0 = cell2hedge[~isBlueCell]
        redge1 = halfedge[redge0, 2]
        redge2 = halfedge[redge1, 2]
        redge3 = halfedge[redge2, 2]
        celltoedge = np.c_[redge0, redge1, redge2, redge3]

        edge0 = halfedge[halfedge[isBlueHEdge, 4], 3]
        edge1 = halfedge[isBlueHEdge, 2]
        edge2 = halfedge[edge0, 3]
        edge3 = halfedge[edge1, 2]
        edge4 = halfedge[halfedge[halfedge[isBlueHEdge, 3], 4], 2]
        edge5 = halfedge[edge4, 2]
        #加密半边扩展
        swap = np.array([0])
        flag = np.array([], dtype = bool)
        while len(swap) or flag.any():
            #红色单元
            bit = markedge[celltoedge].astype(bool)
            dec = np.einsum('ij,j->i',bit,np.array([1, 2, 4, 8])).astype(np.int)
            dec[0]=0
            vaR = valR[dec]
            idx, jdx = np.where(~bit & mapR[dec].astype(bool))
            swap = idx.copy()
            markedge[celltoedge[idx, jdx]] = 1
            markedge[halfedge[markedge.astype(bool), 4]] = 1#TODO
            #蓝色单元
            bit = markedge[np.c_[edge0, edge1, edge2, edge3, edge4,
                edge5]].astype(np.bool)
            dec = np.einsum('ij,j->i',bit,np.array([1, 2, 4, 8, 16,
                32])).astype(np.int)
            vaB = valB[dec]
            flag = ~bit & mapB[dec].astype(bool)
            markedge[edge0[flag[:, 0]]] = 1
            markedge[edge1[flag[:, 1]]] = 1
            markedge[edge2[flag[:, 2]]] = 1
            markedge[edge3[flag[:, 3]]] = 1
            markedge[edge4[flag[:, 4]]] = 1
            markedge[edge5[flag[:, 5]]] = 1
            markedge[halfedge[markedge.astype(bool), 4]] = 1
        markedge[halfedge[edge5[markedge[edge5].astype(bool)], 2]] = 1
        markedge[halfedge[edge4[markedge[edge4].astype(bool)], 3]] = 1
        markedge[halfedge[markedge.astype(bool), 4]] = 1
        #边界上的新节点
        isMainHEdge = (halfedge[:, 5] == 1) # 主半边标记
        flag0 = isMainHEdge & markedge.astype(bool)# 即是主半边, 也是标记加密的半边
        flag1 = halfedge[flag0, 4]
        ec = (node[halfedge[flag0, 0]] + node[halfedge[flag1, 0]])/2
        NE1 = len(ec)
        node = np.r_[node, ec]

        nlevel = np.r_[nlevel, np.zeros(NE1, dtype=np.int)]
        nlevel[NN:] = np.maximum(nlevel[halfedge[flag0, 0]], nlevel[halfedge[flag1, 0]])+1
        #被加密半边及加密节点编号
        markedge[flag0] = np.arange(NN, NN+NE1)
        markedge[flag1] = np.arange(NN, NN+NE1)

        #边界上的新半边
        halfedge1 = np.zeros([NE1*2, 6], dtype = np.int)
        halfedge1[:NE1, 0] = np.arange(NN, NN+NE1)
        halfedge1[NE1:, 0] = np.arange(NN, NN+NE1)
        halfedge1[:NE1, 4] = halfedge[flag0, 4]
        halfedge1[NE1:, 4] = halfedge[flag1, 4]
        halfedge1[:NE1, 5] = 1
        halfedge = np.r_[halfedge, halfedge1]
        halfedge[halfedge[NE:, 4], 4] = np.arange(NE,
                NE+NE1*2)
        newhalfedge = np.zeros(markedge.shape[0], dtype = np.int)
        newhalfedge[flag0] = markedge[flag0]-NN+NE
        newhalfedge[flag1] = markedge[flag1]-NN+NE+NE1

        #将蓝色单元变为红色单元
        flag = markedge[edge0]!=0
        NC1 = flag.sum()
        flaag = np.arange(halfedge.shape[0])[halfedge[:, 1]==14]

        halfedge[isBlueHEdge[flag], 0] = markedge[edge1[flag]]
        halfedge20 = np.zeros([NC1, 6], dtype=np.int)
        halfedge21 = np.zeros([NC1, 6], dtype=np.int)
        halfedge20[:, 0] = markedge[edge0[flag]]
        halfedge21[:, 0] = halfedge[halfedge[isBlueHEdge, 4], 0][flag]
        halfedge20[:, 1] = np.arange(NC, NC+NC1)
        halfedge21[:, 1] = halfedge[edge0[flag], 1]
        halfedge[edge0[flag], 1] = np.arange(NC, NC+NC1)
        halfedge[halfedge[isBlueHEdge[flag], 4], 1] = np.arange(NC, NC+NC1)
        halfedge[newhalfedge[edge1[flag]], 1] = np.arange(NC, NC+NC1)
        halfedge[newhalfedge[edge0[flag]], 1] = halfedge[edge2[flag], 1]
        halfedge20[:, 2] = edge0[flag]
        halfedge21[:, 2] = halfedge[halfedge[isBlueHEdge[flag], 4], 2]
        halfedge20[:, 3] = halfedge[isBlueHEdge[flag], 4]
        halfedge21[:, 3] = newhalfedge[edge0[flag]]
        halfedge20[:, 5] = 1
        halfedge = np.r_[halfedge, halfedge20, halfedge21]
        halfedge[NE+NE1*2:NE+NE1*2+NC1, 4] = np.arange(NE+NE1*2+NC1, NE+NE1*2+NC1*2)
        halfedge[NE+NE1*2+NC1:, 4] =  np.arange(NE+NE1*2, NE+NE1*2+NC1)
        halfedge[edge0[flag], 2] = newhalfedge[edge1[flag]]
        halfedge[newhalfedge[edge1[flag]], 2] = halfedge[isBlueHEdge, 4][flag]
        halfedge[halfedge[isBlueHEdge[flag], 4], 2] =  np.arange(NE+NE1*2,
                NE+NE1*2+NC1)
        halfedge[edge2[flag], 2] = newhalfedge[edge0[flag]]
        halfedge[newhalfedge[edge0[flag]], 2] = np.arange(NE+NE1*2+NC1,
                NE+NE1*2+NC1*2)

        flag1 = halfedge[:, 1]!=0
        halfedge[halfedge[flag1, 2], 3] = np.arange(NE+NE1*2+NC1*2)[flag1]
        #加密后的边去除标记
        markedge[edge0[flag]] = 0
        markedge[edge1[flag]] = 0
        #新单元的层数
        clevel1 = np.zeros(NC1)
        clevel1 = clevel[halfedge[edge5, 1]]
        clevel = np.r_[clevel, clevel1]
        markedge = np.r_[markedge, np.zeros(NE1*2+NC1*2)]
        #设置起始边
        cell2hedgetest = np.r_[cell2hedgetest, np.zeros(NC1, dtype=np.int)]
        cell2hedgetest[halfedge[edge2[flag], 1]] = newhalfedge[edge0[flag]]
        cell2hedgetest[NC:] = newhalfedge[edge1[flag]]

        #网格加密生成新的内部半边
        celltoedge0 = cell2hedge
        celltoedge0[halfedge[edge1, 1]] = edge3
        celltoedge0[halfedge[edge2, 1]] = halfedge[edge2, 3]
        celltoedge0[halfedge[edge4, 1]] = halfedge[edge4, 3]
        celltoedge1 = halfedge[celltoedge0, 2]
        celltoedge2 = halfedge[celltoedge1, 2]
        celltoedge3 = halfedge[celltoedge2, 2]
        celltoedge = np.c_[celltoedge0, celltoedge1, celltoedge2, celltoedge3]
        bit = markedge[celltoedge].astype(bool)
        valueR = np.einsum('ij, j->i', bit, np.array([1,2,4,8]))
        valueR[0] = 0
        red = np.where(valueR==15)[0]
        bluer = np.where(valueR==3)[0]
        bluel = np.where(valueR==12)[0]
        midnode = (node[halfedge[celltoedge[valueR!=0, 0], 0]]+
                node[halfedge[celltoedge[valueR!=0, 1], 0]]+
                node[halfedge[celltoedge[valueR!=0, 2], 0]]+
                node[halfedge[celltoedge[valueR!=0, 3], 0]])/4
        node = np.r_[node, midnode]
        nlevel = np.r_[nlevel, np.zeros(len(midnode), dtype=np.int)]
        midnode = np.zeros(NC)
        midnode[valueR!=0] = np.arange(NN+NE1, node.shape[0])

        #新单元编号
        dx = np.zeros(NC, dtype=np.int)
        dx[red] = 3
        dx[bluer] = 2
        dx[bluel] = 2
        dx = np.cumsum(dx)
        clevel = np.r_[clevel, np.zeros(dx[-1], dtype=np.int)]
        cell2hedgetest = np.r_[cell2hedgetest, np.zeros(dx[-1], dtype=np.int)]
        dx += NC+NC1
        #新半边编号
        hdx = np.zeros(NC, dtype=np.int)
        hdx[red] = 8
        hdx[bluer] = 6
        hdx[bluel] = 6
        hdx = np.cumsum(hdx)
        halfedge = np.r_[halfedge, np.zeros([hdx[-1], 6], dtype=np.int)]
        hdx += NE + NE1*2 +NC1*2

        #设置起始边
        cell2hedgetest[red] = newhalfedge[celltoedge[red, 1]]
        cell2hedgetest[dx[red]-3] = newhalfedge[celltoedge[red, 2]]
        cell2hedgetest[dx[red]-2] = newhalfedge[celltoedge[red, 3]]
        cell2hedgetest[dx[red]-1] = newhalfedge[celltoedge[red, 0]]
        cell2hedgetest[bluer] =newhalfedge[celltoedge[bluer, 1]]
        cell2hedgetest[dx[bluer]-2] = celltoedge[bluer, 2]
        cell2hedgetest[dx[bluer]-1] =newhalfedge[celltoedge[bluer, 0]]
        cell2hedgetest[bluel] = celltoedge[bluel, 0]
        cell2hedgetest[dx[bluel]-2] = newhalfedge[celltoedge[bluel, 2]]
        cell2hedgetest[dx[bluel]-1] =newhalfedge[celltoedge[bluel, 3]]

        #红色单元
        halfedge[celltoedge[red, 1], 1] = dx[red]-3
        halfedge[celltoedge[red, 2], 1] = dx[red]-2
        halfedge[celltoedge[red, 3], 1] = dx[red]-1
        halfedge[newhalfedge[celltoedge[red, 0]], 1] = dx[red]-1
        halfedge[newhalfedge[celltoedge[red, 1]], 1] = red #halfedge[celltoedge[red, 0], 1]
        halfedge[newhalfedge[celltoedge[red, 2]], 1] = dx[red]-3
        halfedge[newhalfedge[celltoedge[red, 3]], 1] = dx[red]-2
        halfedge = halfedge.astype(np.int)

        halfedge[hdx[red]-8, :] = np.c_[markedge[celltoedge[red, 0]], red,
                celltoedge[red, 0], hdx[red]-3, hdx[red]-4,np.zeros(red.shape[0])+1]
        halfedge[hdx[red]-7, :] = np.c_[markedge[celltoedge[red, 1]], dx[red]-3,
                celltoedge[red, 1], hdx[red]-2, hdx[red]-3, np.zeros(red.shape[0])+1]
        halfedge[hdx[red]-6, :] = np.c_[markedge[celltoedge[red, 2]], dx[red]-2,
                celltoedge[red, 2], hdx[red]-1, hdx[red]-2, np.zeros(red.shape[0])+1]
        halfedge[hdx[red]-5, :] = np.c_[markedge[celltoedge[red, 3]], dx[red]-1,
                celltoedge[red, 3], hdx[red]-4, hdx[red]-1, np.zeros(red.shape[0])+1]
        halfedge[hdx[red]-4, :] = np.c_[midnode[red], dx[red]-1,
                hdx[red]-5, newhalfedge[celltoedge[red, 0]], hdx[red]-8,
                np.zeros(red.shape[0])]
        halfedge[hdx[red]-3, :] = np.c_[midnode[red], red,
                hdx[red]-8, newhalfedge[celltoedge[red, 1]], hdx[red]-7,
                np.zeros(red.shape[0])]
        halfedge[hdx[red]-2, :] = np.c_[midnode[red], dx[red]-3,
                hdx[red]-7, newhalfedge[celltoedge[red, 2]], hdx[red]-6,
                np.zeros(red.shape[0])]
        halfedge[hdx[red]-1, :] = np.c_[midnode[red], dx[red]-2,
                hdx[red]-6, newhalfedge[celltoedge[red, 3]] , hdx[red]-5,
                np.zeros(red.shape[0])]

        halfedge[newhalfedge[celltoedge[red, 0]], 2:4] = np.c_[hdx[red]-4,
                celltoedge[red, 3]]
        halfedge[newhalfedge[celltoedge[red, 1]], 2:4] = np.c_[hdx[red]-3,
                celltoedge[red, 0]]
        halfedge[newhalfedge[celltoedge[red, 2]], 2:4] = np.c_[hdx[red]-2,
                celltoedge[red, 1]]
        halfedge[newhalfedge[celltoedge[red, 3]], 2:4] = np.c_[hdx[red]-1,
                celltoedge[red, 2]]
        halfedge[celltoedge[red, 0], 2:4] = np.c_[newhalfedge[celltoedge[red,
            1]], hdx[red]-8]
        halfedge[celltoedge[red, 1], 1:4] = np.c_[dx[red]-3,
            newhalfedge[celltoedge[red,2]], hdx[red]-7]
        halfedge[celltoedge[red, 2], 1:4] = np.c_[dx[red]-2,
            newhalfedge[celltoedge[red,3]], hdx[red]-6]
        halfedge[celltoedge[red, 3], 1:4] = np.c_[dx[red]-1,
            newhalfedge[celltoedge[red,0]], hdx[red]-5]

        #蓝色单元:bluer
        halfedge[celltoedge[bluer, 1], 1] = dx[bluer]-2
        halfedge[celltoedge[bluer, 2], 1] = dx[bluer]-2
        halfedge[celltoedge[bluer, 3], 1] = dx[bluer]-1
        halfedge[newhalfedge[celltoedge[bluer, 0]], 1] = dx[bluer]-1
        halfedge[newhalfedge[celltoedge[bluer, 1]], 1] = bluer #halfedge[celltoedge[bluer, 0], 1]

        halfedge[hdx[bluer]-6, :] = np.c_[markedge[celltoedge[bluer, 0]], bluer,
                celltoedge[bluer, 0], hdx[bluer]-2, hdx[bluer]-3,
                np.zeros(bluer.shape[0])+1]
        halfedge[hdx[bluer]-5, :] = np.c_[markedge[celltoedge[bluer, 1]],dx[bluer]-2,
                celltoedge[bluer, 1], hdx[bluer]-1, hdx[bluer]-2,
                np.zeros(bluer.shape[0])+1]
        halfedge[hdx[bluer]-4, :] = np.c_[halfedge[celltoedge[bluer, 2], 0],
                dx[bluer]-1, celltoedge[bluer, 3], hdx[bluer]-3, hdx[bluer]-1,
                np.zeros(bluer.shape[0])+1]
        halfedge[hdx[bluer]-3, :] = np.c_[midnode[bluer], dx[bluer]-1,
                hdx[bluer]-4, newhalfedge[celltoedge[bluer, 0]], hdx[bluer]-6,
                np.zeros(bluer.shape[0])]
        halfedge[hdx[bluer]-2, :] = np.c_[midnode[bluer], bluer,
                hdx[bluer]-6, newhalfedge[celltoedge[bluer, 1]], hdx[bluer]-5,
                np.zeros(bluer.shape[0])]
        halfedge[hdx[bluer]-1, :] = np.c_[midnode[bluer], dx[bluer]-2,
                hdx[bluer]-5, celltoedge[bluer, 2], hdx[bluer]-4,
                np.zeros(bluer.shape[0])]

        halfedge[newhalfedge[celltoedge[bluer, 0]], 2:4] = np.c_[hdx[bluer]-3,
                celltoedge[bluer, 3]]
        halfedge[newhalfedge[celltoedge[bluer, 1]], 2:4] = np.c_[hdx[bluer]-2,
                celltoedge[bluer, 0]]
        halfedge[celltoedge[bluer, 0], 2:4] = np.c_[newhalfedge[celltoedge[bluer,
            1]], hdx[bluer]-6]
        halfedge[celltoedge[bluer, 1], 1:4:2] = np.c_[dx[bluer]-2, hdx[bluer]-5]
        halfedge[celltoedge[bluer, 2], 1:3] = np.c_[dx[bluer]-2, hdx[bluer]-1]
        halfedge[celltoedge[bluer, 3], 1:4] = np.c_[dx[bluer]-1,
            newhalfedge[celltoedge[bluer,0]], hdx[bluer]-4]

        #蓝色单元:bluel
        halfedge[celltoedge[bluel, 1], 1] = dx[bluel]-2
        halfedge[celltoedge[bluel, 2], 1] = dx[bluel]-1
        halfedge[newhalfedge[celltoedge[bluel, 2]], 1] = dx[bluel]-2
        halfedge[newhalfedge[celltoedge[bluel, 3]], 1] = dx[bluel]-1

        halfedge[hdx[bluel]-6, :] = np.c_[halfedge[celltoedge[bluel, 0], 0],
                dx[bluel]-2, celltoedge[bluel, 1], hdx[bluel]-2, hdx[bluel]-3,
                np.zeros(bluel.shape[0])+1]
        halfedge[hdx[bluel]-5, :] = np.c_[markedge[celltoedge[bluel, 2]],
                dx[bluel]-1, celltoedge[bluel, 2], hdx[bluel]-1, hdx[bluel]-2,
                np.zeros(bluel.shape[0])+1]
        halfedge[hdx[bluel]-4, :] = np.c_[markedge[celltoedge[bluel, 3]], bluel,
                celltoedge[bluel, 3], hdx[bluel]-3, hdx[bluel]-1,
                np.zeros(bluel.shape[0])+1]
        halfedge[hdx[bluel]-3, :] = np.c_[midnode[bluel], bluel, hdx[bluel]-4,
                celltoedge[bluel, 0], hdx[bluel]-6,
                np.zeros(bluel.shape[0])]
        halfedge[hdx[bluel]-2, :] = np.c_[midnode[bluel], dx[bluel]-2,
                hdx[bluel]-6, newhalfedge[celltoedge[bluel, 2]], hdx[bluel]-5,
                np.zeros(bluel.shape[0])]
        halfedge[hdx[bluel]-1, :] = np.c_[midnode[bluel], dx[bluel]-1,
                hdx[bluel]-5, newhalfedge[celltoedge[bluel, 3]], hdx[bluel]-4,
                np.zeros(bluel.shape[0])]

        halfedge[newhalfedge[celltoedge[bluel, 2]], 1:4] = np.c_[dx[bluel]-2,
                hdx[bluel]-2, celltoedge[bluel, 1]]
        halfedge[newhalfedge[celltoedge[bluel, 3]], 1:4] = np.c_[dx[bluel]-1,
                hdx[bluel]-1, celltoedge[bluel, 2]]
        halfedge[celltoedge[bluel, 0], 2] = hdx[bluel]-3
        halfedge[celltoedge[bluel, 1], 1:4] = np.c_[dx[bluel]-2,
                newhalfedge[celltoedge[bluel,2]], hdx[bluel]-6]
        halfedge[celltoedge[bluel, 2], 1:4] = np.c_[dx[bluel]-1,
                newhalfedge[celltoedge[bluel, 3]], hdx[bluel]-5]
        halfedge[celltoedge[bluel, 3], 3] = hdx[bluel]-4

        idx = np.where(valueR!=0)[0]
        clevel[idx] +=1
        clevel[dx[red]-1] = clevel[red]
        clevel[dx[red]-2] = clevel[red]
        clevel[dx[red]-3] = clevel[red]
        clevel[dx[bluel]-1] = clevel[bluel]
        clevel[dx[bluel]-2] = clevel[bluel]
        clevel[dx[bluer]-1] = clevel[bluer]
        clevel[dx[bluer]-2] = clevel[bluer]
        nlevel[NN+NE1:] = clevel[idx]
        subdomain = np.ones(dx[-1], dtype = np.int)
        subdomain[0] = 0
        #外部半边的顺序
        flag = halfedge[:, 1]==0
        NE0 = len(halfedge)
        markedge = np.r_[markedge, np.zeros(NE0-len(markedge), dtype=np.int)]
        flag = flag & markedge.astype(np.bool)
        flag = np.arange(NE0)[flag]
        halfedge[newhalfedge[flag], 2] = flag
        halfedge[newhalfedge[flag], 3] = halfedge[flag, 3]
        halfedge[halfedge[flag, 3], 2] = newhalfedge[flag]
        halfedge[flag, 3] = newhalfedge[flag]

        self.nodedata['level'] = nlevel
        self.celldata['level'] = clevel
        self.ds.halfedge = halfedge.astype(np.int)
        self.node = node

        self.ds.reinit(len(node), subdomain, halfedge.astype(np.int))

        self.ds.cell2hedge = cell2hedgetest

   

    def coarsen_quad(self, isMarkedCell):
        pass
    
    def refine_poly(self, isMarkedCell, data=None, dflag=False, inplace=True):
        """

        Parameters
        ----------
        isMarkedCell : np.ndarray, bool,
            len(isMarkedCell) == len(self.ds.subdomain)
        """

        NC = self.number_of_all_cells() 
        assert len(isMarkedCell) == NC

        NN = self.number_of_nodes()
        NE = self.number_of_edges()
        
        bc = self.cell_barycenter(return_all=True) # 返回所有单元的重心, 包括外
                                                   # 部无界区域和区域中的洞区域

        # 单元和半边的层标记信息
        clevel = self.celldata['level'] # 注意这里是所有的单元的层信息
        hlevel = self.halfedgedata['level']

        halfedge = self.ds.halfedge
        subdomain = self.ds.subdomain

        isMainHEdge = (halfedge[:, 5] == 1) # 主半边标记

        # 标记边
        # 当前半边的层标记小于等于所属单元的层标记
        flag0 = (hlevel - clevel[halfedge[:, 1]]) <= 0 
        # 前一半边的层标记小于等于所属单元的层标记 
        flag1 = (hlevel[halfedge[:, 3]] - clevel[halfedge[:, 1]]) <= 0
        # 标记加密的半边
        isMarkedHEdge = isMarkedCell[halfedge[:, 1]] & flag0 & flag1 
        # 标记加密的半边的相对半边也需要标记 
        flag = ~isMarkedHEdge & isMarkedHEdge[halfedge[:, 4]]
        isMarkedHEdge[flag] = True

        node = self.entity('node')
        # 即是主半边, 也是标记加密的半边
        flag0 = isMainHEdge & isMarkedHEdge
        idx = halfedge[flag0, 4]
        ec = (node[halfedge[flag0, 0]] + node[halfedge[idx, 0]])/2
        NE1 = len(ec)
        
        if data is not None:
            NV = self.ds.number_of_vertices_of_all_cells()
            for key, value in data.items():
                # 定义在节点的数据进行简单插值
                evalue = (value[halfedge[flag0, 0]] + value[halfedge[idx, 0]])/2
                cvalue = np.zeros(NC, dtype=self.ftype)
                np.add.at(cvalue, halfedge[:, 1], value[halfedge[:, 0]])
                cvalue /= NV
                data[key] = np.concatenate((value, evalue, cvalue[isMarkedCell]), axis=0)

        #细分边
        halfedge1 = np.zeros((2*NE1, 6), dtype=self.itype)
        flag1 = isMainHEdge[isMarkedHEdge] # 标记加密边中的主半边
        halfedge1[flag1, 0] = range(NN, NN+NE1) # 新的节点编号
        idx0 = np.argsort(idx) # 当前边的对偶边的从小到大进行排序
        halfedge1[~flag1, 0] = halfedge1[flag1, 0][idx0] # 按照排序

        hlevel1 = np.zeros(2*NE1, dtype=self.itype)
        hlevel1[flag1] = np.maximum(hlevel[flag0], hlevel[halfedge[flag0, 3]]) + 1
        hlevel1[~flag1] = np.maximum(hlevel[idx], hlevel[halfedge[idx, 3]])[idx0]+1

        halfedge1[:, 1] = halfedge[isMarkedHEdge, 1]
        halfedge1[:, 3] = halfedge[isMarkedHEdge, 3] # 前一个 
        halfedge1[:, 4] = halfedge[isMarkedHEdge, 4] # 对偶边
        halfedge1[:, 5] = halfedge[isMarkedHEdge, 5] # 主边标记

        halfedge[isMarkedHEdge, 3] = range(2*NE, 2*NE + 2*NE1)
        idx = halfedge[isMarkedHEdge, 4] # 原始对偶边
        halfedge[isMarkedHEdge, 4] = halfedge[idx, 3]  # 原始对偶边的前一条边是新的对偶边

        halfedge = np.r_['0', halfedge, halfedge1]
        halfedge[halfedge[:, 3], 2] = range(2*NE+2*NE1)
        hlevel = np.r_[hlevel, hlevel1]

        if dflag:
            self.halfedgedata['level'] = hlevel 
            self.node = np.r_['0', node, ec]
            self.ds.reinit(NN+NE1,  subdomain, halfedge)
            return

        # 细分单元
        flag = (hlevel - clevel[halfedge[:, 1]]) == 1
        N = halfedge.shape[0]
        NV = np.zeros(NC, dtype=self.itype)
        np.add.at(NV, halfedge[:, 1], flag)
        NHE = sum(NV[isMarkedCell])
        halfedge1 = np.zeros((2*NHE, 6), dtype=self.itype)
        hlevel1 = np.zeros(2*NHE, dtype=self.itype)
        
        NC1 = isMarkedCell.sum() # 加密单元个数
        
        # 当前为标记单元的可以加密的半边
        flag0 = flag & isMarkedCell[halfedge[:, 1]]
        idx0, = np.nonzero(flag0)
        nex0 = halfedge[flag0, 2]
        pre0 = halfedge[flag0, 3]
        subdomain = np.r_['0', subdomain[~isMarkedCell], subdomain[halfedge[flag0, 1]]]
        
        # 修改单元的编号
        cellidx = halfedge[idx0, 1] #需要加密的单元编号
        halfedge[idx0, 1] = range(NC, NC + NHE)
        clevel[isMarkedCell] += 1
        
        idx1 = idx0.copy()
        pre = halfedge[idx1, 3]
        flag0 = ~flag[pre] # 前一个是不需要细分的半边
        while np.any(flag0):
            idx1[flag0] = pre[flag0]
            pre = halfedge[idx1, 3]
            flag0 = ~flag[pre] 
            halfedge[idx1, 1] = halfedge[idx0, 1]
            
        nex1 = halfedge[idx1, 2] # 当前半边的下一个半边
        pre1 = halfedge[idx1, 3] # 当前半边的上一个半边

        cell2newNode = np.full(NC, NN+NE1, dtype=self.itype)
        cell2newNode[isMarkedCell] += range(isMarkedCell.sum()) 
        
        halfedge[idx0, 2] = range(N, N+NHE) # idx0 的下一个半边的编号
        halfedge[idx1, 3] = range(N+NHE, N+2*NHE) # idx1 的上一个半边的编号
        
        halfedge1[:NHE, 0] = cell2newNode[cellidx]
        halfedge1[:NHE, 1] = halfedge[idx0, 1]
        halfedge1[:NHE, 2] = halfedge[idx1, 3]
        halfedge1[:NHE, 3] = idx0
        halfedge1[:NHE, 4] = halfedge[nex0, 3]
        halfedge1[:NHE, 5] = 1
        hlevel1[:NHE] = clevel[cellidx]

        halfedge1[NHE:, 0] = halfedge[pre1, 0]
        halfedge1[NHE:, 1] = halfedge[idx1, 1]
        halfedge1[NHE:, 2] = idx1
        halfedge1[NHE:, 3] = halfedge[idx0, 2]
        halfedge1[NHE:, 4] = halfedge[pre1, 2]
        halfedge1[NHE:, 5] = 0
        hlevel1[NHE:] = clevel[cellidx]

        clevel = np.r_['0', clevel[~isMarkedCell], clevel[cellidx]]
        halfedge = np.r_['0', halfedge, halfedge1]

        flag = np.zeros(NC+NHE, dtype=np.bool)
        flag[halfedge[:, 1]] = True

        idxmap = np.zeros(NC+NHE, dtype=self.itype)
        nc = flag.sum()
        idxmap[flag] = range(nc)
        halfedge[:, 1] = idxmap[halfedge[:, 1]]

        self.halfedgedata['level'] = np.r_[hlevel, hlevel1]
        self.celldata['level'] = clevel
        self.node = np.r_['0', node, ec, bc[isMarkedCell]]
        self.ds.reinit(NN+NE1+NC1, subdomain, halfedge)
    
    def coarsen_poly(self, isMarkedCell, dflag=True):

        NC = self.number_of_all_cells()
        NN = self.number_of_nodes()
        NE = self.number_of_edges()
        
        hlevel = self.halfedgedata['level']
        clevel = self.celldata['level']

        halfedge = self.ds.halfedge
        subdomain = self.ds.subdomain

        # 可以移除的网格节点
        # 在理论上, 可以移除点周围的单元所属子区是相同的, TODO: make sure about it

        isRNode = np.ones(NN, dtype=np.bool) 
        flag = (hlevel == clevel[halfedge[:, 1]])
        np.logical_and.at(isRNode, halfedge[:, 0], flag)
        flag = (hlevel == hlevel[halfedge[:, 4]])
        np.logical_and.at(isRNode, halfedge[:, 0], flag)
        flag = isMarkedCell[halfedge[:, 1]]
        np.logical_and.at(isRNode, halfedge[:, 0], flag)

        nn = isRNode.sum()
        
        if nn > 0:
            # 重新标记要移除的单元
            isMarkedCell = np.zeros(NC+nn, dtype=np.bool)
            isMarkedHEdge = isRNode[halfedge[:, 0]] | isRNode[halfedge[halfedge[:, 4], 0]]
            isMarkedCell[halfedge[isMarkedHEdge, 1]] = True
            
            # 没有被标记的单元个数
            nc = sum(~isMarkedCell[:NC])

            # 更新粗化后单元的所属子区域的信息
            nsd = np.zeros(NN, dtype=self.itype)
            nsd[halfedge[:, 0]] = subdomain[halfedge[:, 1]]
            subdomain = np.zeros(nc+nn, dtype=self.itype)
            subdomain[:nc] = self.ds.subdomain[~isMarkedCell[:NC]]
            subdomain[nc:] = nsd[isRNode]

            # 粗化后单元的新编号: NC:NC+nn 
            nidxmap = np.arange(NN)
            nidxmap[isRNode] = range(NC, NC+nn)
            cidxmap = np.arange(NC)
            isRHEdge = isRNode[halfedge[:, 0]]
            cidxmap[halfedge[isRHEdge, 1]] = nidxmap[halfedge[isRHEdge, 0]]
            halfedge[:, 1] = cidxmap[halfedge[:, 1]] 

            # 更新粗化后单元的层数
            nlevel = np.zeros(NN, dtype=self.itype)
            nlevel[halfedge[:, 0]] = hlevel
            level = nlevel[isRNode] - 1
            level[level < 0] = 0
            clevel = np.zeros(nc+nn, dtype=self.itype)
            clevel[:nc] = self.celldata['level'][~isMarkedCell[:NC]]
            clevel[nc:] = level 


            # 重设下一个半边 halfedge[:, 2] 和前一个半边 halfedge[:, 3]
            nex = halfedge[:, 2] # 当前半边的下一个半边编号
            flag = isRNode[halfedge[nex, 0]] # 如果下一个半边的指向的节点是要移除的节点
            # 当前半边的下一个半边修改为:下一个半边的对偶半边的下一个半边
            halfedge[flag, 2] = halfedge[halfedge[nex[flag], 4], 2]
            # 下一个半边的前一个半边是当前半边
            halfedge[halfedge[flag, 2], 3], = np.nonzero(flag) 

            nidxmap = np.zeros(NN, dtype=self.itype)
            # 标记进一步要移除的半边
            idx = np.arange(2*NE)
            flag = ~isMarkedHEdge
            flag = flag & (halfedge[halfedge[halfedge[halfedge[:, 2], 4], 2], 4] == idx)
            flag = flag & (hlevel > hlevel[halfedge[:, 2]])
            flag = flag & (hlevel > hlevel[halfedge[:, 3]])

            nex = halfedge[flag, 2]
            pre = halfedge[flag, 3]
            dua = halfedge[flag, 4]

            halfedge[pre, 2] = nex
            halfedge[nex, 3] = pre
            halfedge[nex, 4] = dua

            isMarkedHEdge[flag] = True
            isRNode[halfedge[flag, 0]] = True
            NN -= nn + flag.sum()//2

            # 对节点重新编号
            nidxmap[~isRNode] = range(NN)
            halfedge[:, 0] = nidxmap[halfedge[:, 0]]

            # 对半边重新编号
            ne = sum(~isMarkedHEdge)
            eidxmap = np.arange(2*NE)
            eidxmap[~isMarkedHEdge] = range(ne)
            halfedge = halfedge[~isMarkedHEdge]
            halfedge[:, 2:5] = eidxmap[halfedge[:, 2:5]]

            # 对单元重新编号
            isKeepedCell = np.zeros(NC+nn+1, dtype=np.bool)
            isKeepedCell[halfedge[:, 1]] = True
            cidxmap = np.zeros(NC+nn+1, dtype=self.itype)
            NC = sum(isKeepedCell)
            cidxmap[isKeepedCell] = range(NC)
            halfedge[:, 1] = cidxmap[halfedge[:, 1]]

            # 更新层信息
            self.halfedgedata['level'] = hlevel[~isMarkedHEdge] 
            self.celldata['level'] = clevel

            # 更新节点和半边数据结构信息
            self.node = self.node[~isRNode]
            self.ds.reinit(NN, subdomain, halfedge)

    def mark_helper(self, idx):
        NC = self.number_of_cells()
        flag = np.zeros(NC, dtype=np.bool)
        flag[idx] = True
        nc = self.number_of_all_cells() 
        isMarkedCell = np.zeros(nc, dtype=np.bool)
        isMarkedCell[self.ds.cellstart:] = flag
        return isMarkedCell


    def refine_marker(self, eta, theta, method="L2"):
        NC = self.number_of_all_cells() 
        isMarkedCell = np.zeros(NC, dtype=np.bool)
        isMarkedCell[self.ds.cellstart:] = mark(eta, theta, method=method)
        return isMarkedCell

    def add_halfedge_plot(self, axes,
        index=None, showindex=False,
        nodecolor='r', edgecolor=['r', 'k'], markersize=20,
        fontsize=8, fontcolor='k', multiindex=None, linewidth=0.5):

        show_halfedge_mesh(axes, self,
                index=index, showindex=showindex,
                nodecolor=nodecolor, edgecolor=edgecolor, markersize=markersize,
                fontsize=fontsize, fontcolor=fontcolor, 
                multiindex=multiindex, linewidth=linewidth)

    def print(self):
        cell, cellLocation = self.entity('cell')
        print("cell:\n", cell)
        print("cellLocation:\n", cellLocation)
        print("cell2edge:\n", self.ds.cell_to_edge(sparse=False))
        print("cell2hedge:\n")
        for i, val in enumerate(self.ds.cell2hedge[:-1]):
            print(i, ':', val)

        print("edge:")
        for i, val in enumerate(self.entity('edge')):
            print(i, ":", val)

class HalfEdgeMesh2dDataStructure():

    def __init__(self, NN, subdomain, halfedge, NV=None):
        self.init(NN, subdomain, halfedge, NV)

    def reinit(self, NN, subdomain, halfedge, NV=None):
        self.init(NN, subdomain, halfedge, NV)

    def init(self, NN, subdomain, halfedge, NV=None):

        self.itype = halfedge.dtype

        self.NN = NN
        self.NE = len(halfedge)//2
        self.NF = self.NE
        self.subdomain = subdomain 

        # 区域内部的单元标记, 这里默认排前面的都是洞, 或者外部无界区域.
        idx, = np.nonzero(subdomain == 0)
        if len(idx) == 0:
            self.cellstart = 0 
        elif len(idx) == 1:
            self.cellstart = idx[0] + 1
        else:
            raise ValueError("The number of unbounded doamin is bigger than 1!")

        self.NC = len(subdomain) - self.cellstart # 区域内单元的个数
        self.hflag = subdomain[halfedge[:, 1]] > 0 # 所属单元是区域内部单元的半边标记

        NC = len(subdomain) # 实际单元个数, 包括外部无界区域和洞
        self.cidxmap = -np.ones(NC, dtype=self.itype) # 单元指标映射数组
        self.cidxmap[self.cellstart:] = range(self.NC)
        self.halfedge = halfedge
        
        self.cell2hedge = np.zeros(NC, dtype=self.itype)   # 存储每个单元的起始半边
        self.cell2hedge[halfedge[:, 1]] = range(2*self.NE) # 的编号

        if NV is None:
            NC = self.NC
            halfedge = self.halfedge
            hflag = self.hflag
            cidxmap = self.cidxmap
            self.NV = np.zeros(NC, dtype=self.itype)
            np.add.at(self.NV, cidxmap[halfedge[hflag, 1]], 1)
        else:
            assert NV == 3 or NV == 4
            self.NV = NV


    def number_of_all_cells(self):
        return len(self.subdomain)

    def number_of_vertices_of_all_cells(self):
        NC = self.number_of_all_cells() 
        halfedge = self.halfedge
        NV = np.zeros(NC, dtype=self.itype)
        np.add.at(NV, halfedge[:, 1], 1)
        return NV

    def number_of_vertices_of_cells(self):
        return self.NV

    def number_of_nodes_of_cells(self):
        return self.NV

    def number_of_edges_of_cells(self):
        return self.NV

    def number_of_faces_of_cells(self):
        return self.NV

    def cell_to_node(self, return_sparse=False):
        NN = self.NN
        NC = self.NC
        halfedge = self.halfedge
        hflag = self.hflag
        cstart = self.cellstart
        cidxmap = self.cidxmap

        if return_sparse:
            val = np.ones(hflag.sum(), dtype=np.bool)
            I = cidxmap[halfedge[hflag, 1]]
            J = halfedge[hflag, 0]
            cell2node = csr_matrix((val, (I, J)), shape=(NC, NN), dtype=np.bool)
            return cell2node
        elif type(self.NV) is np.ndarray: # polygon mesh
            cellLocation = np.zeros(NC+1, dtype=self.itype)
            cellLocation[1:] = np.cumsum(self.NV)
            cell2node = np.zeros(cellLocation[-1], dtype=self.itype)
            current = self.cell2hedge[cstart:]
            idx = cellLocation[:-1].copy()
            cell2node[idx] = halfedge[current, 0]
            NV0 = np.ones(NC, dtype=self.itype)
            isNotOK = NV0 < self.NV
            while isNotOK.sum() > 0:
               current[isNotOK] = halfedge[current[isNotOK], 2]
               idx[isNotOK] += 1
               NV0[isNotOK] += 1
               cell2node[idx[isNotOK]] = halfedge[current[isNotOK], 0]
               isNotOK = (NV0 < self.NV)
            return cell2node, cellLocation
        elif self.NV == 3: # tri mesh
            cell2node = np.zeros(NC, 3)
            current = self.cell2hedge[cstart:]
            cell2node[:, 0] = halfedge[current, 0]
            current = halfedge[current, 2]
            cell2node[:, 1] = halfedge[current, 0]
            current = halfedge[current, 2]
            cell2node[:, 2] = halfedge[current, 0]
            return cell2node
        elif self.NV == 4: # quad mesh
            cell2node = np.zeros(NC, 3)
            current = self.cell2hedge[cstart:]
            cell2node[:, 0] = halfedge[current, 0]
            current = halfedge[current, 2]
            cell2node[:, 1] = halfedge[current, 0]
            current = halfedge[current, 2]
            cell2node[:, 2] = halfedge[current, 0]
            current = halfedge[current, 2]
            cell2node[:, 3] = halfedge[current, 0]
            return cell2node
        else:
            raise ValueError('The property NV should be None, 3 or 4! But the NV is {}'.format(self.NV))

    def cell_to_edge(self, return_sparse=False):
        NE = self.NE
        NC = self.NC

        halfedge = self.halfedge
        cstart = self.cellstart
        hflag = self.hflag

        J = np.zeros(2*NE, dtype=self.itype)
        isMainHEdge = (halfedge[:, 5] == 1)
        J[isMainHEdge] = range(NE)
        J[halfedge[isMainHEdge, 4]] = range(NE)
        if return_sparse:
            val = np.ones(2*NE, dtype=np.bool)
            cell2edge = csr_matrix((val[hflag], (halfedge[hflag, 1],
                J[hflag])), shape=(NC, NE), dtype=np.bool)
            return cell2edge
        elif type(self.NV) is np.ndarray:

            cellLocation = np.zeros(NC+1, dtype=self.itype)
            cellLocation[1:] = np.cumsum(self.NV)

            cell2edge = np.zeros(cellLocation[-1], dtype=self.itype)
            current = halfedge[self.cell2hedge[cstart:], 2] # 下一个边
            idx = cellLocation[:-1]
            cell2edge[idx] = J[current]
            NV0 = np.ones(NC, dtype=self.itype)
            isNotOK = NV0 < self.NV
            while isNotOK.sum() > 0:
                current[isNotOK] = halfedge[current[isNotOK], 2]
                idx[isNotOK] += 1
                NV0[isNotOK] += 1
                cell2edge[idx[isNotOK]] = J[current[isNotOK]]
                isNotOK = (NV0 < self.NV)
            return cell2edge
        elif self.NV == 3: # tri mesh
            cell2edge = np.zeros(NC, 3)
            current = self.cell2hedge[cstart:]
            cell2edge[:, 1] = J[current]
            cell2edge[:, 2] = J[halfedge[current, 2]]
            cell2edge[:, 0] = J[halfedge[current, 3]]
            return cell2edge
        elif self.NV == 4: # quad mesh
            cell2edge = np.zeros(NC, 4)
            current = self.cell2hedge[cstart:]
            cell2edge[:, 3] = J[current]
            current = halfedge[current, 2]
            cell2edge[:, 0] = J[current] 
            current = halfedge[current, 2]
            cell2edge[:, 1] = J[current]
            current = halfedge[current, 2]
            cell2edge[:, 2] = J[current]
            return cell2edge
        else:
            raise ValueError('The property NV should be None, 3 or 4! But the NV is {}'.format(self.NV))

    def cell_to_face(self, return_sparse=True):
        return self.cell_to_edge(return_sparse=return_sparse)

    def cell_to_cell(self, return_sparse=True):
        NC = self.NC
        halfedge = self.halfedge
        cstart = self.cellstart
        hflag = self.hflag
        cidxmap = self.cidxmap
        if return_sparse:
            flag = hflag & hflag[halfedge[:, 4]]
            val = np.ones(flag.sum(), dtype=np.bool)
            I = halfedge[flag, 1]
            J = halfedge[halfedge[flag, 4], 1]
            cell2cell = coo_matrix((val, (I, J)), shape=(NC, NC), dtype=np.bool)
            cell2cell+= coo_matrix((val, (J, I)), shape=(NC, NC), dtype=np.bool)
            return cell2cell.tocsr()
        elif type(self.NV) is np.ndarray:
            cellLocation = np.zeros(NC+1, dtype=self.itype)
            cellLocation[1:] = np.cumsum(self.NV)
            cell2cell = np.zeros(cellLocation[-1], dtype=self.itype)
            current = halfedge[self.cell2hedge[cstart:], 2] # 下一个边
            idx = cellLocation[:-1]
            cell2cell[idx] = cidxmap[halfedge[halfedge[current, 4], 1]]
            NV0 = np.ones(NC, dtype=self.itype)
            isNotOK = NV0 < self.NV
            while isNotOK.sum() > 0:
                current[isNotOK] = halfedge[current[isNotOK], 2]
                idx[isNotOK] += 1
                NV0[isNotOK] += 1
                cell2cell[idx[isNotOK]] = cidxmap[halfedge[halfedge[current[isNotOK], 4], 1]]
                isNotOK = (NV0 < self.NV)
            idx = np.repeat(range(NC), self.NV)
            flag = (cell2cell == -1)
            cell2cell[flag] = idx[flag]
            return cell2cell
        elif self.NV == 3: # tri mesh
            cell2cell = np.zeros(NC, 3)
            current = self.cell2hedge[cstart:]
            cell2cell[:, 1] = cidxmap[halfedge[halfedge[current, 4], 1]]
            cell2cell[:, 2] = cidxmap[halfedge[halfedge[halfedge[current, 2], 4], 1]]
            cell2cell[:, 0] = cidxmap[halfedge[halfedge[halfedge[current, 3], 4], 1]]
            idx = np.repeat(range(NC), 3).reshape(NC, 3)
            flag = (cell2cell == -1)
            cell2cell[flag] = idx[flag]
            return cell2cella
        elif self.NV == 4: # quad mesh
            cell2cell = np.zeros(NC, 4)
            current = self.cell2hedge[cstart:]
            cell2cell[:, 3] = cidxmap[halfedge[halfedge[current, 4], 1]] 
            current = halfedge[current, 2]
            cell2cell[:, 0] = cidxmap[halfedge[halfedge[current, 4], 1]] 
            current = halfedge[current, 2]
            cell2cell[:, 1] = cidxmap[halfedge[halfedge[current, 4], 1]] 
            current = halfedge[current, 2]
            cell2cell[:, 2] = cidxmap[halfedge[halfedge[current, 4], 1]]
            idx = np.repeat(range(NC), 4).reshape(NC, 4)
            flag = (cell2cell == -1)
            cell2cell[flag] = idx[flag]
            return cell2cell
        else:
            raise ValueError('The property NV should be None, 3 or 4! But the NV is {}'.format(self.NV))

    def edge_to_node(self, return_sparse=False):
        NN = self.NN
        NE = self.NE
        halfedge = self.halfedge
        isMainHEdge = halfedge[:, 5] == 1
        if return_sparse == False:
            edge = np.zeros((NE, 2), dtype=self.itype)
            edge[:, 0] = halfedge[halfedge[isMainHEdge, 4], 0]
            edge[:, 1] = halfedge[isMainHEdge, 0]
            return edge
        else:
            val = np.ones(NE, dtype=np.bool)
            edge2node = coo_matrix((val, (range(NE), halfedge[isMainHEdge,0])), shape=(NE, NN), dtype=np.bool)
            edge2node+= coo_matrix((val, (range(NE), halfedge[halfedge[isMainHEdge, 4], 0])), shape=(NE, NN), dtype=np.bool)
            return edge2node.tocsr()

    def edge_to_edge(self):
        edge2node = self.edge_to_node()
        return edge2node*edge2node.tranpose()

    def edge_to_cell(self):
        NE = self.NE
        NC = self.NC
        halfedge = self.halfedge
        cstart = self.cellstart
        cidxmap = self.cidxmap

        J = np.zeros(2*NE, dtype=self.itype)
        isMainHEdge = (halfedge[:, 5] == 1)
        J[isMainHEdge] = range(NE)
        J[halfedge[isMainHEdge, 4]] = range(NE)
        edge2cell = np.full((NE, 4), -1, dtype=self.itype)
        edge2cell[J[isMainHEdge], 0] = cidxmap[halfedge[isMainHEdge, 1]]
        edge2cell[J[halfedge[isMainHEdge, 4]], 1] = cidxmap[halfedge[halfedge[isMainHEdge, 4], 1]]
        if type(self.NV) is np.ndarray:
            current = halfedge[self.cell2hedge[cstart], 2] # 下一个边
            end = current.copy()
            lidx = np.zeros_like(current)
            isNotOK = np.ones_like(current, dtype=np.bool)
            while np.any(isNotOK):
                idx = J[current[isNotOK]]
                flag = (halfedge[current[isNotOK], 5] == 1)
                edge2cell[idx[flag], 2] = lidx[isNotOK][flag]
                edge2cell[idx[~flag], 3] = lidx[isNotOK][~flag]
                current[isNotOK] = halfedge[current[isNotOK], 2]
                lidx[isNotOK] += 1
                isNotOK = (current != end)
        elif self.NV == 3:
            current = self.cell2hedge[cstart]
            idx = J[current]
            flag = halfedge[current, 5] == 1 
            edge2cell[idx[flag], 2] = 1
            edge2cell[idx[~flag], 3] = 1

            idx = J[halfedge[current, 2]]
            flag = halfedge[halfedge[current, 2], 5] == 1 
            edge2cell[idx[flag], 2] = 2
            edge2cell[idx[~flag], 3] = 2

            idx = J[halfedge[current, 3]]
            flag = halfedge[halfedge[current, 3], 5] == 1 
            edge2cell[idx[flag], 2] = 0
            edge2cell[idx[~flag], 3] = 0
        elif self.NV == 4:
            current = self.cell2hedge[cstart]
            idx = J[current]
            flag = halfedge[current, 5] == 1 
            edge2cell[idx[flag], 2] = 3
            edge2cell[idx[~flag], 3] = 3

            current = halfedge[current, 2]
            idx = J[current]
            flag = halfedge[current, 5] == 1 
            edge2cell[idx[flag], 2] = 0
            edge2cell[idx[~flag], 3] = 0

            current = halfedge[current, 2]
            idx = J[current]
            flag = halfedge[current, 5] == 1 
            edge2cell[idx[flag], 2] = 1
            edge2cell[idx[~flag], 3] = 1

            current = halfedge[current, 2]
            idx = J[current]
            flag = halfedge[current, 5] == 1 
            edge2cell[idx[flag], 2] = 2
            edge2cell[idx[~flag], 3] = 2
        else:
            raise ValueError('The property NV should be None, 3 or 4! But the NV is {}'.format(self.NV))

        flag = edge2cell[:, 1] == -1
        edge2cell[flag, 1] = edge2cell[flag, 0]
        edge2cell[flag, 3] = edge2cell[flag, 2]
        return edge2cell

    def node_to_node(self):
        NN = self.NN
        NE = self.NE
        edge = self.edge_to_node()
        I = edge[:, 0:2].flat
        J = edge[:, 1::-1].flat
        val = np.ones(2*NE, dtype=np.bool)
        node2node = csr_matrix((val, (I, J)), shape=(NN, NN), dtype=np.bool)
        return node2node

    def node_to_cell(self, sparse=True):
        NN = self.NN
        NC = self.NC
        halfedge =  self.halfedge
        hflag = self.hflag
        cidxmap = self.cidxmap

        val = np.ones(hflag.sum(), dtype=np.bool)
        I = halfedge[hflag, 0]
        J = cidxmap[halfedge[hflag, 1]]
        node2cell = csr_matrix((val, (I.flat, J.flat)), shape=(NN, NC), dtype=np.bool)
        return node2cell

    def boundary_node_flag(self):
        NN = self.NN
        halfedge =  self.halfedge
        hflag = self.hflag
        isBdHEdge = hflag & (~hflag[halfedge[:, 4]])
        isBdNode = np.zeros(NN, dtype=np.bool)
        isBdNode[halfedge[isBdHEdge, 0]] = True 
        return isBdNode

    def boundary_edge_flag(self):
        NE = self.NE
        halfedge =  self.halfedge
        hflag = self.hflag
        isBdHEdge = hflag & (~hflag[halfedge[:, 4]])

        J = np.zeros(2*NE, dtype=self.itype)
        isMainHEdge = (halfedge[:, 5] == 1)
        J[isMainHEdge] = range(NE)
        return J[isBdHEdge] 

    def boundary_edge(self):
        edge = self.edge_to_node()
        return edge[self.boundary_edge_index()]

    def boundary_cell_flag(self):
        NC = self.NC
        halfedge =  self.halfedge
        hflag = self.hflag
        isBdHEdge = hflag & (~hflag[halfedge[:, 4]])

        isBdCell = np.zeros(NC, dtype=np.bool)
        idx = cidxmap[halfedge[isBdHEdge, 1]]
        isBdCell[idx] = True
        return isBdCell

    def boundary_node_index(self):
        isBdNode = self.boundary_node_flag()
        idx, = np.nonzero(isBdNode)
        return idx

    def boundary_edge_index(self):
        isBdEdge = self.boundary_edge_flag()
        idx, = np.nonzero(isBdEdge)
        return idx

    def boundary_cell_index(self):
        isBdCell = self.boundary_cell_flag()
        idx, = np.nonzero(isBdCell)
        return idx
