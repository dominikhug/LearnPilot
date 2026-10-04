<script setup lang="ts">
import { Handle, Position } from '@vue-flow/core'
import type { Concept } from '../api'
import { stateIcon, stateLabel } from '../concepts'

defineProps<{ data: Concept; selected: boolean }>()
</script>

<template>
  <!-- A real button, so nodes are reachable by keyboard; its click reaches Vue Flow's node-click. -->
  <button
    class="concept-node"
    :class="[data.state, { selected }]"
    :aria-pressed="selected"
    :title="stateLabel[data.state]"
  >
    <Handle type="target" :position="Position.Top" :connectable="false" />
    <span class="concept-node-icon" aria-hidden="true">{{ stateIcon[data.state] }}</span>
    <span class="concept-node-name">{{ data.name }}</span>
    <span class="visually-hidden">, {{ stateLabel[data.state] }}</span>
    <Handle type="source" :position="Position.Bottom" :connectable="false" />
  </button>
</template>
