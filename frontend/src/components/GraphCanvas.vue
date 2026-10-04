<script setup lang="ts">
import dagre from '@dagrejs/dagre'
import {
  type Edge,
  MarkerType,
  type Node,
  type NodeMouseEvent,
  VueFlow,
  type VueFlowStore,
} from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { Concept, ConceptGraph } from '../api'
import ConceptNode from './ConceptNode.vue'

const props = defineProps<{ graph: ConceptGraph; selectedId: number | null }>()
const emit = defineEmits<{ select: [id: number] }>()

const NODE_WIDTH = 200
const NODE_HEIGHT = 64

// Top-to-bottom layout: prerequisites above the concepts that build on them.
const nodes = computed<Node<Concept>[]>(() => {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'TB', nodesep: 32, ranksep: 64 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const concept of props.graph.concepts) {
    g.setNode(String(concept.id), { width: NODE_WIDTH, height: NODE_HEIGHT })
  }
  for (const edge of props.graph.edges) {
    g.setEdge(String(edge.from_concept_id), String(edge.to_concept_id))
  }
  dagre.layout(g)

  return props.graph.concepts.map((concept) => {
    const { x, y } = g.node(String(concept.id))
    return {
      id: String(concept.id),
      type: 'concept',
      data: concept,
      position: { x: x - NODE_WIDTH / 2, y: y - NODE_HEIGHT / 2 },
      selected: concept.id === props.selectedId,
    }
  })
})

const edges = computed<Edge[]>(() =>
  props.graph.edges.map((edge) => ({
    id: `${edge.from_concept_id}-${edge.to_concept_id}`,
    source: String(edge.from_concept_id),
    target: String(edge.to_concept_id),
    markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--muted)' },
    class: { uncertain: edge.confidence < 0.5 },
  })),
)

function onNodeClick({ node }: NodeMouseEvent) {
  emit('select', Number(node.id))
}

// Opening the side panel narrows the canvas; refit so no node ends up out of view.
const canvas = ref<HTMLElement>()
let flow: VueFlowStore | undefined
// Deferred a frame: Vue Flow measures the new size with its own observer first.
const resizeObserver = new ResizeObserver(() => requestAnimationFrame(() => flow?.fitView()))
onMounted(() => canvas.value && resizeObserver.observe(canvas.value))
onUnmounted(() => resizeObserver.disconnect())
</script>

<template>
  <div ref="canvas" class="graph-canvas">
    <VueFlow
      :nodes="nodes"
      :edges="edges"
      :nodes-draggable="false"
      :nodes-connectable="false"
      :nodes-focusable="false"
      :edges-focusable="false"
      :elements-selectable="false"
      :min-zoom="0.2"
      :max-zoom="1.5"
      fit-view-on-init
      @node-click="onNodeClick"
      @pane-ready="flow = $event"
    >
      <template #node-concept="nodeProps">
        <ConceptNode :data="nodeProps.data" :selected="nodeProps.data.id === selectedId" />
      </template>
    </VueFlow>
  </div>
</template>
