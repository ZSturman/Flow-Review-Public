#!/usr/bin/env ruby
# frozen_string_literal: true
#
# Flow Review's Xcode project helper.
#
# This is the one place Flow Review touches project.pbxproj / .xcscheme files.
# It shells out to the battle-tested `xcodeproj` gem (the same library used by
# CocoaPods and fastlane) instead of hand-parsing pbxproj text, because pbxproj
# is a de-facto format with a lot of edge cases that a bespoke parser would get
# wrong. All state is read from and written back to the real project file, so
# the project is always the source of truth -- Flow Review keeps no shadow copy.
#
# Protocol: a single JSON object on stdin describing the command, a single
# JSON object on stdout with the result (or {"error": "..."} + non-zero exit).

require 'json'
require 'fileutils'

begin
  require 'xcodeproj'
rescue LoadError
  puts JSON.generate(
    error: "The 'xcodeproj' Ruby gem is not installed. Install it with: gem install xcodeproj",
    missing_gem: true,
  )
  exit 1
end

def fail_with(message)
  puts JSON.generate(error: message)
  exit 1
end

def platform_symbol(sdk_or_platform)
  case sdk_or_platform.to_s.downcase
  when /mac|osx/ then :osx
  else :ios
  end
end

def target_platform(target)
  sdkroot = target.resolved_build_setting('SDKROOT')&.values&.compact&.first
  platform_symbol(sdkroot || 'iphoneos')
end

def target_bundle_id(target)
  target.resolved_build_setting('PRODUCT_BUNDLE_IDENTIFIER')&.values&.compact&.first
end

def target_deployment_target(target, platform)
  key = platform == :osx ? 'MACOSX_DEPLOYMENT_TARGET' : 'IPHONEOS_DEPLOYMENT_TARGET'
  target.resolved_build_setting(key)&.values&.compact&.first
end

def scheme_paths(project_path)
  base = File.join(project_path, 'xcshareddata', 'xcschemes')
  return [] unless Dir.exist?(base)
  Dir.glob(File.join(base, '*.xcscheme'))
end

def cmd_inspect(args)
  project_path = args.fetch('project_path')
  project = Xcodeproj::Project.open(project_path)

  targets = project.targets.map do |t|
    {
      'name' => t.name,
      'product_type' => t.product_type,
      'platform' => target_platform(t).to_s,
      'bundle_id' => target_bundle_id(t),
      'is_app' => t.product_type == 'com.apple.product-type.application',
      'is_ui_test' => t.product_type == 'com.apple.product-type.bundle.ui-testing',
      'is_unit_test' => t.product_type == 'com.apple.product-type.bundle.unit-test',
    }
  end

  schemes = scheme_paths(project_path).map { |p| File.basename(p, '.xcscheme') }

  {
    'targets' => targets,
    'schemes' => schemes,
  }
end

def find_or_create_group(root_group, path_components)
  group = root_group
  path_components.each do |component|
    child = group.children.find { |c| c.respond_to?(:display_name) && c.display_name == component && c.isa == 'PBXGroup' }
    child ||= group.new_group(component, component)
    group = child
  end
  group
end

def cmd_ensure_ui_test_target(args)
  project_path = args.fetch('project_path')
  app_target_name = args.fetch('app_target_name')
  ui_test_target_name = args.fetch('ui_test_target_name')
  scheme_name = args['scheme_name'] || app_target_name

  project = Xcodeproj::Project.open(project_path)
  existing = project.targets.find { |t| t.name == ui_test_target_name }
  if existing
    return {
      'created' => false,
      'name' => existing.name,
      'group_path' => ui_test_target_name,
    }
  end

  app_target = project.targets.find { |t| t.name == app_target_name }
  fail_with("App target '#{app_target_name}' not found") unless app_target

  platform = target_platform(app_target)
  deployment_target = target_deployment_target(app_target, platform)
  app_bundle_id = target_bundle_id(app_target) || "com.example.#{app_target_name.downcase}"

  ui_target = project.new_target(
    :ui_test_bundle,
    ui_test_target_name,
    platform,
    deployment_target,
    project.products_group,
    :swift,
    ui_test_target_name,
  )
  ui_target.add_dependency(app_target)

  ui_target.build_configuration_list.build_configurations.each do |config|
    config.build_settings['GENERATE_INFOPLIST_FILE'] = 'YES'
    config.build_settings['PRODUCT_BUNDLE_IDENTIFIER'] = "#{app_bundle_id}.flowreviewuitests"
    config.build_settings['TEST_TARGET_NAME'] = app_target_name
    config.build_settings['SWIFT_VERSION'] = '5.0'
    config.build_settings['CODE_SIGN_STYLE'] = 'Automatic'
    config.build_settings['CLANG_ENABLE_MODULES'] = 'YES'
    if platform == :osx
      config.build_settings['COMBINE_HIDPI_IMAGES'] = 'YES'
    end
  end

  # File-system group to hold the target's sources (mirrors normal Xcode layout).
  group = find_or_create_group(project.main_group, [ui_test_target_name])
  group.set_source_tree('<group>')

  # Register the test bundle in the main app's scheme (create scheme if absent).
  scheme_file = File.join(project_path, 'xcshareddata', 'xcschemes', "#{scheme_name}.xcscheme")
  scheme = File.exist?(scheme_file) ? Xcodeproj::XCScheme.new(scheme_file) : Xcodeproj::XCScheme.new
  test_ref = Xcodeproj::XCScheme::TestAction::TestableReference.new(ui_target)
  already = scheme.test_action.testables.any? { |t| t.buildable_references.first&.target_name == ui_test_target_name }
  scheme.test_action.add_testable(test_ref) unless already
  FileUtils.mkdir_p(File.dirname(scheme_file))
  scheme.save_as(project_path, scheme_name, true)

  project.save

  {
    'created' => true,
    'name' => ui_target.name,
    'group_path' => ui_test_target_name,
    'bundle_id' => "#{app_bundle_id}.flowreviewuitests",
  }
end

# Keeps one generated group's file references in sync with what's actually on
# disk. Only files inside this group are ever touched, so this is safe to
# re-run on every `flow-review run` without disturbing hand-written files
# elsewhere in the target.
def cmd_sync_generated_group(args)
  project_path = args.fetch('project_path')
  host_target_name = args.fetch('host_target_name')
  group_name = args.fetch('group_name')
  folder_path = args.fetch('folder_path') # absolute path on disk
  project_relative_folder_path = args['project_relative_folder_path']
  wanted_files = args.fetch('files') # array of basenames currently on disk

  project = Xcodeproj::Project.open(project_path)
  target = project.targets.find { |t| t.name == host_target_name }
  fail_with("Target '#{host_target_name}' not found") unless target

  parent_group = find_or_create_group(project.main_group, [host_target_name])
  gen_group = parent_group.children.find { |c| c.respond_to?(:display_name) && c.display_name == group_name }
  unless gen_group
    initial_path = project_relative_folder_path || folder_path
    gen_group = parent_group.new_group(group_name, initial_path)
  end
  # Store SOURCE_ROOT-relative paths in project.pbxproj. Absolute paths work
  # only on the machine that ran `init`, making the generated target unusable
  # when the project is cloned or checked out by another developer.
  if project_relative_folder_path
    gen_group.set_source_tree('SOURCE_ROOT')
    gen_group.set_path(project_relative_folder_path)
  else
    gen_group.set_path(folder_path)
    gen_group.set_source_tree('<absolute>')
  end

  sources_phase = target.source_build_phase
  resources_phase = target.resources_build_phase

  existing_refs = gen_group.children.select { |c| c.isa == 'PBXFileReference' }
  existing_by_name = existing_refs.each_with_object({}) { |r, h| h[r.display_name] = r }

  # Migrate groups generated by versions that wrote machine-specific absolute
  # child paths. The group itself now carries the SOURCE_ROOT-relative path.
  if project_relative_folder_path
    existing_by_name.each do |basename, ref|
      ref.set_path(basename)
      ref.set_source_tree('<group>')
    end
  end

  added = []
  wanted_files.each do |basename|
    next if existing_by_name.key?(basename)
    # A child reference inherits the generated group's SOURCE_ROOT-relative
    # path. Older projects may contain absolute references; they are repaired
    # the next time the generated group is synchronized.
    file_ref = gen_group.new_reference(basename)
    if basename.end_with?('.swift')
      sources_phase.add_file_reference(file_ref)
    else
      resources_phase.add_file_reference(file_ref)
    end
    added << basename
  end

  removed = []
  existing_by_name.each do |basename, ref|
    next if wanted_files.include?(basename)
    [sources_phase, resources_phase].each do |phase|
      build_file = phase.files.find { |bf| bf.file_ref == ref }
      phase.remove_file_reference(ref) if build_file
    end
    ref.remove_from_project
    removed << basename
  end

  project.save

  { 'added' => added, 'removed' => removed }
end

def main
  input = JSON.parse($stdin.read)
  command = input.fetch('command')
  args = input.fetch('args', {})

  result = case command
           when 'inspect' then cmd_inspect(args)
           when 'ensure_ui_test_target' then cmd_ensure_ui_test_target(args)
           when 'sync_generated_group' then cmd_sync_generated_group(args)
           else
             fail_with("Unknown command: #{command}")
           end

  puts JSON.generate(result)
rescue StandardError => e
  puts JSON.generate(error: "#{e.class}: #{e.message}", backtrace: e.backtrace&.first(5))
  exit 1
end

main
